from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import TypeAdapter

from ...delivery import (
    DeliveryExecutionSnapshot,
    DeliveryKnowledgeContextSnapshot,
    DeliveryKnowledgeContextUnavailableSnapshot,
    KnowledgePreparationInputV1,
    KnowledgePreparationResult,
)
from ...shared.errors import ProductError
from ...shared.hashes import sha256_bytes, sha256_json
from ...shared.permissions import Role
from ..artifacts import ContentAddressedArtifactStorage
from ..identity import IdentityService
from ..orchestration import KnowledgeContextBinding
from ..projects import ProjectCatalog, ProjectKnowledgeSourceApproval
from ..workcells import DeliveryExecutionSnapshotCompiler
from .context_domain import (
    AdministratorBypassAuthorizationComponent,
    AuthorizationAccessComponent,
    AuthorizationApprovalComponent,
    AuthorizationConnectionComponent,
    KnowledgeAuthorizationStampV1,
    KnowledgeContextRuntimeView,
    KnowledgeContextStageResult,
    MembershipAuthorizationComponent,
)
from .context_repository import SQLiteKnowledgeContextRepository
from .domain import KnowledgeActor
from .index_application import KnowledgeIndexManager
from .index_domain import KnowledgeRetrievalRequest
from .tenant_application import TenantKnowledgeManager

_ACCESS_ADAPTER: TypeAdapter[AuthorizationAccessComponent] = TypeAdapter(
    AuthorizationAccessComponent
)

_TRANSIENT_PREPARATION_ERRORS = frozenset(
    {
        "FEISHU_RATE_LIMITED",
        "FEISHU_TIMEOUT",
        "FEISHU_UNAVAILABLE",
        "KNOWLEDGE_OLLAMA_UNAVAILABLE",
    }
)


class _KnowledgePreparationRetryScheduled(RuntimeError):
    def __init__(self, next_attempt_at: datetime) -> None:
        super().__init__("KNOWLEDGE_CONTEXT_PREPARATION_RETRY_SCHEDULED")
        self.next_attempt_at = next_attempt_at


class KnowledgeAuthorizationResolver:
    """Resolve the independent authorization epochs used by best-effort revoke."""

    def __init__(
        self,
        *,
        identity: IdentityService,
        projects: ProjectCatalog,
        tenant: TenantKnowledgeManager,
    ) -> None:
        self.identity = identity
        self.projects = projects
        self.tenant = tenant

    def initial_access_component(
        self,
        *,
        project_id: str,
        principal_id: str,
        bypass_receipt_id: str | None,
    ) -> AuthorizationAccessComponent:
        membership = self.projects.repository.get_membership(project_id, principal_id)
        if membership is not None:
            return MembershipAuthorizationComponent(
                membership_id=f"{project_id}:{principal_id}",
                version=membership.version,
            )
        user = self.identity.repository.get_user(principal_id)
        if user is None or user.role != Role.ADMINISTRATOR or bypass_receipt_id is None:
            raise _authorization_revoked("授权主体没有有效 Project Membership 或旁路回执")
        receipt = next(
            (
                item
                for item in self.projects.repository.list_access_audits(project_id)
                if item.id == bypass_receipt_id and item.actor_user_id == principal_id
            ),
            None,
        )
        if receipt is None:
            raise _authorization_revoked("Administrator bypass receipt 不存在")
        return AdministratorBypassAuthorizationComponent(
            receipt_id=receipt.id,
            receipt_sha256=sha256_json(receipt.model_dump(mode="json")),
        )

    def resolve(
        self,
        *,
        project_id: str,
        principal_id: str,
        frozen_access_component: dict[str, object],
        frozen_approval_ids: tuple[str, ...] | None = None,
    ) -> KnowledgeAuthorizationStampV1:
        user = self.identity.repository.get_user(principal_id)
        if user is None or not user.enabled:
            raise _authorization_revoked("Delivery Authorized Principal 已停用")
        project = self.projects.repository.get(project_id)
        if project is None or project.lifecycle_status != "active":
            raise _authorization_revoked("Project 不再处于 active 状态")
        frozen = _ACCESS_ADAPTER.validate_python(frozen_access_component)
        membership = self.projects.repository.get_membership(project_id, principal_id)
        if isinstance(frozen, MembershipAuthorizationComponent):
            if membership is None:
                raise _authorization_revoked("Delivery Authorized Principal 已被移出项目")
            access: AuthorizationAccessComponent = MembershipAuthorizationComponent(
                membership_id=f"{project_id}:{principal_id}",
                version=membership.version,
            )
        else:
            if user.role != Role.ADMINISTRATOR or membership is not None:
                raise _authorization_revoked("Administrator bypass 分支已经改变")
            receipt = next(
                (
                    item
                    for item in self.projects.repository.list_access_audits(project_id)
                    if item.id == frozen.receipt_id and item.actor_user_id == principal_id
                ),
                None,
            )
            if receipt is None:
                raise _authorization_revoked("Administrator bypass receipt 已失效")
            receipt_sha = sha256_json(receipt.model_dump(mode="json"))
            if receipt_sha != frozen.receipt_sha256:
                raise _authorization_revoked("Administrator bypass receipt Hash 已漂移")
            access = AdministratorBypassAuthorizationComponent(
                receipt_id=receipt.id,
                receipt_sha256=receipt_sha,
            )
        current_approvals = {
            item.id: item
            for item in self.projects.repository.list_knowledge_source_approvals(project_id)
        }
        selected_approval_ids = (
            tuple(sorted(current_approvals))
            if frozen_approval_ids is None
            else tuple(sorted(set(frozen_approval_ids)))
        )
        approvals: list[AuthorizationApprovalComponent] = []
        connections: dict[str, AuthorizationConnectionComponent] = {}
        for approval_id in selected_approval_ids:
            approval = current_approvals.get(approval_id)
            if approval is None or not approval.enabled or not approval.rag_enabled:
                raise _authorization_revoked("冻结的 Project Knowledge Approval 已失效")
            binding = self.tenant.repository.get_binding(approval.binding_id)
            if binding is None or binding.status != "ready":
                raise _authorization_revoked("Approved Binding 不再可用")
            connection = self.tenant.repository.get_connection(binding.connection_id)
            if connection is None or connection.status != "ready":
                raise _authorization_revoked("Tenant Connection 不再可用")
            scope_sha = sha256_json(
                {
                    "approval_id": approval.id,
                    "project_id": project_id,
                    "binding_id": binding.id,
                    "external_space_id": binding.external_space_id,
                    "root_node_token": binding.root_node_token,
                    "scope_policy": "binding-root-and-descendants-v1",
                }
            )
            approvals.append(
                AuthorizationApprovalComponent(
                    approval_id=approval.id,
                    approval_version=approval.version,
                    binding_id=binding.id,
                    binding_authorization_version=binding.authorization_version,
                    approved_source_scope_sha256=scope_sha,
                )
            )
            connections[connection.id] = AuthorizationConnectionComponent(
                connection_id=connection.id,
                authorization_version=connection.authorization_version,
            )
        payload = {
            "policy_id": "best-effort-revoke-v1",
            "global_identity_policy_revision": 1,
            "project_id": project_id,
            "authorized_principal_id": principal_id,
            "identity_authorization_version": user.authorization_version,
            "global_role": user.role.value,
            "project_authorization_version": (
                self.projects.repository.get_authorization_version(project_id)
            ),
            "access_component": access.model_dump(mode="json"),
            "approvals": [item.model_dump(mode="json") for item in approvals],
            "connections": [
                item.model_dump(mode="json")
                for item in sorted(connections.values(), key=lambda item: item.connection_id)
            ],
        }
        return KnowledgeAuthorizationStampV1(
            policy_id="best-effort-revoke-v1",
            global_identity_policy_revision=1,
            project_id=project_id,
            authorized_principal_id=principal_id,
            identity_authorization_version=user.authorization_version,
            global_role=user.role.value,
            project_authorization_version=(
                self.projects.repository.get_authorization_version(project_id)
            ),
            access_component=access,
            approvals=tuple(approvals),
            connections=tuple(sorted(connections.values(), key=lambda item: item.connection_id)),
            authorization_epoch_hash=sha256_json(payload),
        )


class KnowledgePreparationInputCompiler:
    """Freeze only local, pre-existing facts before Delivery persistence."""

    def __init__(
        self,
        *,
        authorization: KnowledgeAuthorizationResolver,
        projects: ProjectCatalog,
        artifacts: ContentAddressedArtifactStorage,
    ) -> None:
        self.authorization = authorization
        self.projects = projects
        self.artifacts = artifacts

    def compile(
        self,
        *,
        delivery_id: str,
        project_id: str,
        principal_id: str,
        delivery_goal: str,
        base_snapshot: DeliveryExecutionSnapshot,
        bypass_receipt_id: str | None,
    ) -> KnowledgePreparationInputV1:
        project = self.projects.repository.get(project_id)
        if project is None:
            raise _context_error("PROJECT_NOT_FOUND", "Project 不存在")
        if base_snapshot.project_id != project_id:
            raise _context_error(
                "KNOWLEDGE_PREPARATION_PROJECT_MISMATCH",
                "Delivery Execution Snapshot 与 Project 不匹配",
            )
        project_description_snapshot = self.artifacts.put_json(
            {
                "contract_id": "project-description-snapshot-v1",
                "project_id": project.id,
                "project_version": project.version,
                "name": project.name,
                "description": project.description,
            },
            media_type=("application/vnd.agent-team-os.project-description-snapshot+json"),
        )
        access = self.authorization.initial_access_component(
            project_id=project_id,
            principal_id=principal_id,
            bypass_receipt_id=bypass_receipt_id,
        )
        approval_ids = tuple(
            sorted(
                item.id
                for item in self.projects.repository.list_knowledge_source_approvals(project_id)
                if item.enabled and item.rag_enabled
            )
        )
        responsibilities: dict[str, str] = {}
        for stage_path in sorted(base_snapshot.knowledge_context_bindings):
            stage_binding = base_snapshot.workcell_stage_map.get(stage_path)
            workcell_key = (
                stage_binding.get("workcell_key") if isinstance(stage_binding, dict) else None
            )
            workcell = (
                base_snapshot.team_workcells.get(workcell_key)
                if isinstance(workcell_key, str)
                else None
            )
            responsibility = workcell.get("responsibility") if isinstance(workcell, dict) else None
            responsibilities[stage_path] = (
                str(responsibility)
                if isinstance(responsibility, str) and responsibility
                else f"Pipeline Stage {stage_path}"
            )
        payload = {
            "delivery_id": delivery_id,
            "project_id": project_id,
            "project_version": project.version,
            "project_description_snapshot": project_description_snapshot.model_dump(mode="json"),
            "authorized_principal_id": principal_id,
            "delivery_goal": delivery_goal,
            "pipeline_revision_id": base_snapshot.pipeline_revision_id,
            "pipeline_revision_sha256": str(base_snapshot.pipeline_revision_sha256),
            "authorization_access_component": access.model_dump(mode="json"),
            "approved_knowledge_approval_ids": approval_ids,
            "stage_bindings": base_snapshot.knowledge_context_bindings,
            "stage_responsibilities": responsibilities,
        }
        return KnowledgePreparationInputV1.model_validate(
            {
                **payload,
                "input_sha256": sha256_json(payload),
            }
        )


class DeliveryKnowledgeContextPreparationService:
    """Durable preparation-v1 implementation; no external work precedes Delivery."""

    def __init__(
        self,
        repository: SQLiteKnowledgeContextRepository,
        *,
        authorization: KnowledgeAuthorizationResolver,
        projects: ProjectCatalog,
        tenant: TenantKnowledgeManager,
        indexes: KnowledgeIndexManager,
        artifacts: ContentAddressedArtifactStorage,
        snapshot_compiler: DeliveryExecutionSnapshotCompiler,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 1.0,
        lease_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds cannot be negative")
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self.repository = repository
        self.authorization = authorization
        self.projects = projects
        self.tenant = tenant
        self.indexes = indexes
        self.artifacts = artifacts
        self.snapshot_compiler = snapshot_compiler
        self.max_attempts = max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.lease_ttl = lease_ttl

    async def prepare(
        self, preparation_input: KnowledgePreparationInputV1
    ) -> KnowledgePreparationResult:
        while True:
            try:
                return await asyncio.to_thread(self._prepare_sync, preparation_input)
            except _KnowledgePreparationRetryScheduled as retry:
                delay = max(
                    0.0,
                    (retry.next_attempt_at - datetime.now(UTC)).total_seconds(),
                )
                await asyncio.sleep(delay)

    def cancel(self, delivery_id: str) -> None:
        self.repository.cancel(delivery_id, now=datetime.now(UTC))

    def _prepare_sync(
        self, preparation_input: KnowledgePreparationInputV1
    ) -> KnowledgePreparationResult:
        expected_input_sha = sha256_json(
            preparation_input.model_dump(mode="json", exclude={"input_sha256"})
        )
        if expected_input_sha != preparation_input.input_sha256:
            raise _context_error(
                "KNOWLEDGE_PREPARATION_INPUT_HASH_MISMATCH",
                "Knowledge Preparation Input Hash 不匹配",
            )
        binding_hash = sha256_json(preparation_input.stage_bindings)
        now = datetime.now(UTC)
        run = self.repository.create_or_get(
            preparation_input,
            knowledge_binding_hash=binding_hash,
            now=now,
        )
        if run.status == "succeeded":
            if run.final_snapshot is None:
                raise _context_error(
                    "KNOWLEDGE_CONTEXT_PREPARATION_CORRUPT",
                    "成功的 Preparation 缺少冻结 Snapshot",
                )
            return KnowledgePreparationResult(
                preparation_run_id=run.id,
                delivery_execution_snapshot=run.final_snapshot,
            )
        if run.status == "retry_wait":
            if run.next_attempt_at is None:
                raise _context_error(
                    "KNOWLEDGE_CONTEXT_PREPARATION_CORRUPT",
                    "retry_wait Preparation 缺少 next_attempt_at",
                )
            if run.next_attempt_at > now:
                raise _KnowledgePreparationRetryScheduled(run.next_attempt_at)
        if run.status in {"leased", "running"}:
            if run.lease_expires_at is None:
                raise _context_error(
                    "KNOWLEDGE_CONTEXT_PREPARATION_CORRUPT",
                    "运行中的 Preparation 缺少 Lease 到期时间",
                )
            if run.lease_expires_at > now:
                raise _KnowledgePreparationRetryScheduled(run.lease_expires_at)
        if run.status in {"failed", "cancelled"}:
            raise _context_error(
                "KNOWLEDGE_CONTEXT_PREPARATION_TERMINAL",
                f"Knowledge Context Preparation 已进入终态 {run.status}",
            )
        try:
            run = self.repository.acquire(
                run.id,
                lease_owner=f"local:{uuid4()}",
                now=now,
                lease_ttl=self.lease_ttl,
            )
            stamp = self.authorization.resolve(
                project_id=preparation_input.project_id,
                principal_id=preparation_input.authorized_principal_id,
                frozen_access_component=preparation_input.authorization_access_component,
                frozen_approval_ids=preparation_input.approved_knowledge_approval_ids,
            )
            contexts, unavailable = self._prepare_stages(run.id, preparation_input, stamp)
            current_stamp = self.authorization.resolve(
                project_id=preparation_input.project_id,
                principal_id=preparation_input.authorized_principal_id,
                frozen_access_component=preparation_input.authorization_access_component,
                frozen_approval_ids=preparation_input.approved_knowledge_approval_ids,
            )
            if current_stamp.authorization_epoch_hash != stamp.authorization_epoch_hash:
                raise _authorization_revoked("Context Preparation 期间授权版本发生变化")
            base = self.snapshot_compiler.compile(
                preparation_input.project_id,
                preparation_input.pipeline_revision_id,
            )
            if (
                base.pipeline_revision_sha256 != preparation_input.pipeline_revision_sha256
                or base.knowledge_context_bindings != preparation_input.stage_bindings
            ):
                raise _context_error(
                    "KNOWLEDGE_PIPELINE_BINDING_DRIFT",
                    "Published Pipeline Knowledge Binding 已漂移",
                )
            snapshot_payload = base.model_dump(mode="json", exclude={"snapshot_sha256"})
            snapshot_payload.update(
                {
                    "knowledge_contexts": {
                        key: value.model_dump(mode="json") for key, value in contexts.items()
                    },
                    "knowledge_context_unavailable": {
                        key: value.model_dump(mode="json") for key, value in unavailable.items()
                    },
                    "knowledge_authorization_stamp": current_stamp.model_dump(mode="json"),
                    "knowledge_preparation_input_sha256": str(preparation_input.input_sha256),
                }
            )
            snapshot = DeliveryExecutionSnapshot(
                **snapshot_payload,
                snapshot_sha256=sha256_json(snapshot_payload),
            )
            completed = self.repository.succeed(
                run.id,
                stamp=current_stamp,
                final_snapshot_json=snapshot.model_dump_json(),
                now=datetime.now(UTC),
            )
            assert completed.final_snapshot is not None
            return KnowledgePreparationResult(
                preparation_run_id=completed.id,
                delivery_execution_snapshot=completed.final_snapshot,
            )
        except Exception as error:
            code = getattr(error, "code", "KNOWLEDGE_CONTEXT_PREPARATION_FAILED")
            if str(code) in _TRANSIENT_PREPARATION_ERRORS and run.attempt_count < self.max_attempts:
                delay = self.retry_base_delay_seconds * (2 ** max(run.attempt_count - 1, 0))
                next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                self.repository.schedule_retry(
                    run.id,
                    error_code=str(code),
                    next_attempt_at=next_attempt_at,
                    now=datetime.now(UTC),
                )
                raise _KnowledgePreparationRetryScheduled(next_attempt_at) from error
            self.repository.fail(run.id, error_code=str(code), now=datetime.now(UTC))
            raise

    def _prepare_stages(
        self,
        run_id: str,
        preparation_input: KnowledgePreparationInputV1,
        stamp: KnowledgeAuthorizationStampV1,
    ) -> tuple[
        dict[str, DeliveryKnowledgeContextSnapshot],
        dict[str, DeliveryKnowledgeContextUnavailableSnapshot],
    ]:
        existing = {item.stage_path: item for item in self.repository.list_stage_results(run_id)}
        contexts: dict[str, DeliveryKnowledgeContextSnapshot] = {}
        unavailable: dict[str, DeliveryKnowledgeContextUnavailableSnapshot] = {}
        actor_user = self.authorization.identity.repository.get_user(
            preparation_input.authorized_principal_id
        )
        assert actor_user is not None
        actor = KnowledgeActor(user_id=actor_user.id, role=actor_user.role)
        approval_by_id = {
            item.id: item
            for item in self.projects.repository.list_knowledge_source_approvals(
                preparation_input.project_id
            )
        }
        approvals = tuple(approval_by_id[item.approval_id] for item in stamp.approvals)
        project_description = self.artifacts.get_json(
            preparation_input.project_description_snapshot
        )
        if (
            not isinstance(project_description, dict)
            or project_description.get("contract_id") != "project-description-snapshot-v1"
            or project_description.get("project_id") != preparation_input.project_id
            or project_description.get("project_version") != preparation_input.project_version
        ):
            raise _context_error(
                "KNOWLEDGE_PROJECT_DESCRIPTION_SNAPSHOT_INVALID",
                "Project Description Snapshot 与冻结输入不一致",
            )
        for stage_path, raw_binding in sorted(preparation_input.stage_bindings.items()):
            binding = KnowledgeContextBinding.model_validate(raw_binding)
            if stage_path in existing:
                existing_result = existing[stage_path]
                self.artifacts.get_bytes(existing_result.context.artifact_reference)
                if (
                    existing_result.context.authorization_epoch_hash
                    != stamp.authorization_epoch_hash
                ):
                    raise _authorization_revoked(
                        "已冻结 Stage Context 使用了过期 Authorization Stamp"
                    )
                contexts[stage_path] = existing_result.context
                continue
            try:
                contexts[stage_path] = self._prepare_stage_context(
                    run_id=run_id,
                    preparation_input=preparation_input,
                    stamp=stamp,
                    actor=actor,
                    approvals=approvals,
                    binding=binding,
                    stage_path=stage_path,
                    project_description=project_description,
                )
            except Exception as error:
                if binding.required:
                    raise
                error_code = str(getattr(error, "code", "KNOWLEDGE_CONTEXT_OPTIONAL_UNAVAILABLE"))
                receipt = self.artifacts.put_json(
                    {
                        "contract_id": "knowledge-context-unavailable-receipt-v1",
                        "delivery_id": preparation_input.delivery_id,
                        "project_id": preparation_input.project_id,
                        "stage_path": stage_path,
                        "retrieval_policy_revision_id": (binding.retrieval_policy_revision_id),
                        "error_code": error_code,
                        "error_category": _unavailable_category(error_code),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    media_type=("application/vnd.agent-team-os.knowledge-context-unavailable+json"),
                )
                unavailable[stage_path] = DeliveryKnowledgeContextUnavailableSnapshot(
                    stage_path=stage_path,
                    receipt_reference=receipt,
                    error_code=error_code,
                )
        return contexts, unavailable

    def _prepare_stage_context(
        self,
        *,
        run_id: str,
        preparation_input: KnowledgePreparationInputV1,
        stamp: KnowledgeAuthorizationStampV1,
        actor: KnowledgeActor,
        approvals: tuple[ProjectKnowledgeSourceApproval, ...],
        binding: KnowledgeContextBinding,
        stage_path: str,
        project_description: dict[str, object],
    ) -> DeliveryKnowledgeContextSnapshot:
        responsibility = preparation_input.stage_responsibilities.get(stage_path, stage_path)
        query = (
            f"Project Name: {project_description.get('name', '')}\n"
            f"Project Description: {project_description.get('description', '')}\n"
            f"Delivery Goal: {preparation_input.delivery_goal}\n"
            f"Stage Path: {stage_path}\n"
            f"Stage Responsibility: {responsibility}"
        )
        query_sha = sha256_bytes(query.encode("utf-8"))
        retrievals: list[dict[str, object]] = []
        citation_ids: list[str] = []
        consumed = 0
        for approval in sorted(approvals, key=lambda item: item.binding_id):
            allowed_sources = self.tenant.available_source_ids(approval.binding_id)
            retrieval = self.indexes.retrieve(
                actor,
                KnowledgeRetrievalRequest(
                    project_id=preparation_input.project_id,
                    provider_binding_id=approval.binding_id,
                    retrieval_policy_revision_id=binding.retrieval_policy_revision_id,
                    query=query,
                    allowed_source_ids=allowed_sources,
                ),
            )
            selected: list[dict[str, object]] = []
            for hit in retrieval.hits:
                size = len(hit.content.encode("utf-8"))
                if consumed + size > binding.max_context_bytes:
                    continue
                consumed += size
                citation_ids.append(hit.citation_id)
                selected.append(hit.model_dump(mode="json"))
            retrievals.append(
                {
                    "binding_id": approval.binding_id,
                    "approval_id": approval.id,
                    "receipt": retrieval.receipt.model_dump(mode="json"),
                    "hits": selected,
                }
            )
        if not retrievals:
            raise _context_error(
                "KNOWLEDGE_REQUIRED_CONTEXT_UNAVAILABLE",
                f"Stage {stage_path} 没有已批准且可检索的 RAG 来源",
            )
        artifact = self.artifacts.put_json(
            {
                "contract_id": "knowledge-context-v1",
                "contract_version": "1.0.0",
                "trust_class": "external-collaborative",
                "instruction_authority": "none",
                "delivery_id": preparation_input.delivery_id,
                "project_id": preparation_input.project_id,
                "stage_path": stage_path,
                "query": query,
                "query_sha256": str(query_sha),
                "project_description_snapshot": (
                    preparation_input.project_description_snapshot.model_dump(mode="json")
                ),
                "retrieval_policy_revision_id": binding.retrieval_policy_revision_id,
                "knowledge_binding_hash": str(sha256_json(binding.model_dump(mode="json"))),
                "approved_scope": [item.model_dump(mode="json") for item in stamp.approvals],
                "authorization_stamp": stamp.model_dump(mode="json"),
                "retrievals": retrievals,
                "citation_ids": tuple(sorted(set(citation_ids))),
            },
            media_type="application/vnd.agent-team-os.knowledge-context+json",
        )
        context = DeliveryKnowledgeContextSnapshot(
            stage_path=stage_path,
            artifact_reference=artifact,
            citation_ids=tuple(sorted(set(citation_ids))),
            authorization_epoch_hash=stamp.authorization_epoch_hash,
        )
        self.repository.put_stage_result(
            KnowledgeContextStageResult(
                preparation_run_id=run_id,
                stage_path=stage_path,
                query_sha256=query_sha,
                retrieval_policy_revision_id=binding.retrieval_policy_revision_id,
                context=context,
                created_at=datetime.now(UTC),
            )
        )
        return context


class KnowledgeContextRuntimeGuard:
    """Fail closed before an Attempt and before accepting its citations."""

    def __init__(
        self,
        *,
        authorization: KnowledgeAuthorizationResolver,
        artifacts: ContentAddressedArtifactStorage,
    ) -> None:
        self.authorization = authorization
        self.artifacts = artifacts

    def admit(
        self,
        delivery: object,
        stage_path: str,
    ) -> KnowledgeContextRuntimeView | None:
        from ...delivery import DeliveryRun

        current_delivery = DeliveryRun.model_validate(delivery)
        snapshot = current_delivery.delivery_execution_snapshot
        if snapshot is None or stage_path not in snapshot.knowledge_context_bindings:
            return None
        binding = KnowledgeContextBinding.model_validate(
            snapshot.knowledge_context_bindings[stage_path]
        )
        raw_stamp = snapshot.knowledge_authorization_stamp
        if raw_stamp is None:
            raise _authorization_revoked("Delivery Snapshot 缺少 Authorization Stamp")
        frozen_stamp = KnowledgeAuthorizationStampV1.model_validate(raw_stamp)
        current_stamp = self.authorization.resolve(
            project_id=current_delivery.project_id,
            principal_id=frozen_stamp.authorized_principal_id,
            frozen_access_component=frozen_stamp.access_component.model_dump(mode="json"),
            frozen_approval_ids=tuple(item.approval_id for item in frozen_stamp.approvals),
        )
        if current_stamp.authorization_epoch_hash != frozen_stamp.authorization_epoch_hash:
            raise _authorization_revoked("AgentAttempt Admission 时 Authorization Stamp 已漂移")

        context = snapshot.knowledge_contexts.get(stage_path)
        unavailable = snapshot.knowledge_context_unavailable.get(stage_path)
        if context is None:
            if unavailable is not None and not binding.required:
                self.artifacts.get_bytes(unavailable.receipt_reference)
                return None
            raise _context_error(
                "KNOWLEDGE_REQUIRED_CONTEXT_UNAVAILABLE",
                f"Stage {stage_path} 缺少 Required Knowledge Context",
            )
        if unavailable is not None:
            raise _context_error(
                "KNOWLEDGE_CONTEXT_SNAPSHOT_AMBIGUOUS",
                f"Stage {stage_path} 同时存在 Context 与 Unavailable Receipt",
            )
        if context.authorization_epoch_hash != frozen_stamp.authorization_epoch_hash:
            raise _authorization_revoked("Knowledge Context 与冻结 Authorization Stamp 不一致")
        payload = self.artifacts.get_json(context.artifact_reference)
        if not isinstance(payload, dict):
            raise _context_error(
                "KNOWLEDGE_CONTEXT_ARTIFACT_INVALID",
                "Knowledge Context Artifact 不是 JSON object",
            )
        payload_stamp = payload.get("authorization_stamp")
        payload_citations = payload.get("citation_ids")
        expected_citations = tuple(sorted(set(context.citation_ids)))
        if not isinstance(payload_citations, list | tuple) or any(
            not isinstance(item, str) for item in payload_citations
        ):
            raise _context_error(
                "KNOWLEDGE_CONTEXT_ARTIFACT_INVALID",
                "Knowledge Context Artifact 的 Citation 结构无效",
            )
        actual_citations = tuple(sorted(set(payload_citations)))
        if (
            payload.get("contract_id") != "knowledge-context-v1"
            or payload.get("instruction_authority") != "none"
            or payload.get("trust_class") != "external-collaborative"
            or payload.get("delivery_id") != current_delivery.id
            or payload.get("project_id") != current_delivery.project_id
            or payload.get("stage_path") != stage_path
            or actual_citations != expected_citations
            or not isinstance(payload_stamp, dict)
            or payload_stamp.get("authorization_epoch_hash")
            != frozen_stamp.authorization_epoch_hash
        ):
            raise _context_error(
                "KNOWLEDGE_CONTEXT_ARTIFACT_INVALID",
                "Knowledge Context Artifact 与 Delivery Snapshot 不一致",
            )
        return KnowledgeContextRuntimeView(
            stage_path=stage_path,
            artifact_reference=context.artifact_reference,
            content=payload,
            citation_ids=expected_citations,
            authorization_epoch_hash=frozen_stamp.authorization_epoch_hash,
        )

    def validate_citations(
        self,
        delivery: object,
        stage_path: str,
        citation_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        context = self.admit(delivery, stage_path)
        normalized = tuple(sorted(set(citation_ids)))
        if context is None:
            if normalized:
                raise _context_error(
                    "KNOWLEDGE_CITATION_NOT_IN_CONTEXT",
                    "Agent 在没有冻结 Context 的 Stage 中声明了 Citation",
                )
            return ()
        allowed = set(context.citation_ids)
        if not set(normalized).issubset(allowed):
            raise _context_error(
                "KNOWLEDGE_CITATION_NOT_IN_CONTEXT",
                "Agent 返回了不属于冻结 Context 的 Citation",
            )
        if allowed and not normalized:
            raise _context_error(
                "KNOWLEDGE_CITATION_REQUIRED",
                "Agent 消费了非空 Knowledge Context 但没有返回 Citation",
            )
        return normalized


def _authorization_revoked(detail: str) -> ProductError:
    return ProductError(
        code="KNOWLEDGE_AUTHORIZATION_REVOKED",
        title="知识授权已撤销或漂移",
        detail=detail,
        repair="停止接纳当前结果；使用最新权限创建新的 Delivery。",
        status_code=409,
    )


def _unavailable_category(error_code: str) -> str:
    if "AUTHORIZATION" in error_code or "PERMISSION" in error_code:
        return "authorization"
    if "INDEX" in error_code or "MODEL" in error_code or "EMBEDDING" in error_code:
        return "retrieval-runtime"
    if "SOURCE" in error_code or "CONNECTION" in error_code or "FEISHU" in error_code:
        return "provider"
    return "internal"


def _context_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="Delivery Knowledge Context 准备失败",
        detail=detail,
        repair="检查 Pipeline Binding、Approved Scope、Index 与权限状态后创建新 Delivery。",
        status_code=409,
    )
