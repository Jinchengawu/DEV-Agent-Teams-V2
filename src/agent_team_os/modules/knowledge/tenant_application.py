from __future__ import annotations

from datetime import timedelta

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.hashes import sha256_bytes
from ...shared.ids import new_id
from ...shared.permissions import Role
from ..artifacts import ContentAddressedArtifactStorage
from .domain import KnowledgeActor
from .provider_domain import ProviderNode, ProviderSpace
from .provider_ports import ProviderFailure
from .tenant_domain import (
    KnowledgeSyncJob,
    KnowledgeSyncJobRequest,
    TenantConnection,
    TenantConnectionCreate,
    TenantProviderBinding,
    TenantProviderBindingCreate,
    TenantProviderSnapshotRecord,
)
from .tenant_ports import TenantKnowledgeProviderResolver
from .tenant_repository import SQLiteTenantKnowledgeRepository


class TenantKnowledgeManager:
    def __init__(
        self,
        repository: SQLiteTenantKnowledgeRepository,
        *,
        provider_resolver: TenantKnowledgeProviderResolver | None = None,
        artifact_storage: ContentAddressedArtifactStorage | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.provider_resolver = provider_resolver
        self.artifact_storage = artifact_storage
        self.clock = clock or SystemClock()

    def create_connection(
        self, actor: KnowledgeActor, request: TenantConnectionCreate
    ) -> TenantConnection:
        self._require_administrator(actor)
        now = self.clock.now()
        connection_record = TenantConnection(
            id=new_id(),
            provider_kind=request.provider_kind,
            display_name=request.display_name,
            app_id_ref=request.app_id_ref,
            app_secret_ref=request.app_secret_ref,
            status="unverified",
            authorization_version=1,
            version=1,
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
        )
        created = self.repository.create_connection(connection_record)
        if created is None:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_CONFLICT",
                title="Tenant Connection 已存在",
                detail="同一 Provider 与 App ID Reference 已存在连接。",
                repair="刷新连接列表并复用现有连接。",
            )
        return created

    def list_connections(self, actor: KnowledgeActor) -> tuple[TenantConnection, ...]:
        self._require_administrator(actor)
        return self.repository.list_connections()

    def list_connection_spaces(
        self, actor: KnowledgeActor, connection_id: str
    ) -> tuple[ProviderSpace, ...]:
        self._require_administrator(actor)
        connection = self._ready_connection(connection_id)
        assert self.provider_resolver is not None
        try:
            return self.provider_resolver.resolve(connection).list_spaces()
        except ProviderFailure as error:
            raise self._provider_failure(error) from error

    def diagnose_connection(self, actor: KnowledgeActor, connection_id: str) -> TenantConnection:
        self._require_administrator(actor)
        current = self.repository.get_connection(connection_id)
        if current is None:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_NOT_FOUND",
                title="Tenant Connection 不存在",
                detail="指定的 Tenant Connection 已不存在。",
                repair="刷新连接列表后重试。",
                status_code=404,
            )
        if self.provider_resolver is None:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_ADAPTER_UNAVAILABLE",
                title="Tenant Knowledge Adapter 未配置",
                detail="当前运行实例不能诊断 Tenant Connection。",
                repair="配置 Feishu Tenant Adapter 后重试。",
                status_code=503,
            )
        now = self.clock.now()
        error_code: str | None = None
        status = "ready"
        try:
            provider = self.provider_resolver.resolve(current)
            provider.list_spaces()
        except ProviderFailure as error:
            status = "degraded"
            error_code = error.code
        diagnosed = current.model_copy(
            update={
                "status": status,
                "authorization_version": current.authorization_version
                + int(current.status != status),
                "version": current.version + 1,
                "updated_at": now,
                "last_diagnosed_at": now,
                "last_error_code": error_code,
            }
        )
        try:
            self.repository.update_connection(diagnosed, current.version)
        except RuntimeError as error:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_VERSION_CONFLICT",
                title="Tenant Connection 版本冲突",
                detail="连接在诊断期间已被更新。",
                repair="刷新连接后重新诊断。",
            ) from error
        return diagnosed

    def create_binding(
        self, actor: KnowledgeActor, request: TenantProviderBindingCreate
    ) -> TenantProviderBinding:
        self._require_administrator(actor)
        connection = self._ready_connection(request.connection_id)
        assert self.provider_resolver is not None
        provider = self.provider_resolver.resolve(connection)
        try:
            spaces = provider.list_spaces()
            if not any(space.external_id == request.external_space_id for space in spaces):
                raise ProductError(
                    code="KNOWLEDGE_SOURCE_SCOPE_DENIED",
                    title="知识空间不在 Tenant App 可见范围内",
                    detail="指定 Space 未通过当前 Tenant Connection 的目录探测。",
                    repair="重新 Diagnose 并从可见 Space 中选择。",
                    status_code=403,
                )
            nodes = provider.list_nodes(request.external_space_id)
            if request.root_node_token is not None and not any(
                node.external_id == request.root_node_token for node in nodes
            ):
                raise ProductError(
                    code="KNOWLEDGE_SOURCE_SCOPE_DENIED",
                    title="知识根节点不在可见范围内",
                    detail="指定根节点未通过当前 Tenant Connection 的目录探测。",
                    repair="从可见节点中重新选择根节点。",
                    status_code=403,
                )
        except ProviderFailure as error:
            raise self._provider_failure(error) from error
        now = self.clock.now()
        binding = TenantProviderBinding(
            id=new_id(),
            connection_id=connection.id,
            display_name=request.display_name,
            external_space_id=request.external_space_id,
            root_node_token=request.root_node_token,
            status="ready",
            authorization_version=1,
            version=1,
            replaces_binding_id=request.replaces_binding_id,
            created_by=actor.user_id,
            created_at=now,
            updated_at=now,
            last_permission_probe_at=now,
        )
        created = self.repository.create_binding(
            binding,
            _filter_nodes(nodes, request.root_node_token),
        )
        if created is None:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_CONFLICT",
                title="Tenant Provider Binding 已存在",
                detail="同一 Connection、Space 和根节点已经绑定。",
                repair="刷新 Binding 列表并复用现有记录。",
            )
        return created

    def list_bindings(self, actor: KnowledgeActor) -> tuple[TenantProviderBinding, ...]:
        self._require_administrator(actor)
        return self.repository.list_bindings()

    def require_binding(self, binding_id: str) -> None:
        self._binding(binding_id)

    def available_source_ids(self, binding_id: str) -> tuple[str, ...]:
        self._binding(binding_id)
        now = self.clock.now()
        return self.repository.list_active_source_ids(
            binding_id,
            permission_probe_not_before=now - timedelta(minutes=30),
        )

    def list_binding_nodes(
        self, actor: KnowledgeActor, binding_id: str
    ) -> tuple[ProviderNode, ...]:
        self._require_administrator(actor)
        binding = self._binding(binding_id)
        return self.repository.list_binding_nodes(binding.id)

    def list_project_binding_nodes(
        self, actor: KnowledgeActor, binding_id: str
    ) -> tuple[ProviderNode, ...]:
        del actor
        binding = self._binding(binding_id)
        return self.repository.list_binding_nodes(binding.id)

    def refresh_binding(self, actor: KnowledgeActor, binding_id: str) -> TenantProviderBinding:
        self._require_administrator(actor)
        binding = self._binding(binding_id)
        connection = self._ready_connection(binding.connection_id)
        assert self.provider_resolver is not None
        try:
            nodes = self.provider_resolver.resolve(connection).list_nodes(binding.external_space_id)
        except ProviderFailure as error:
            raise self._provider_failure(error) from error
        try:
            return self.repository.refresh_binding_nodes(
                binding,
                _filter_nodes(nodes, binding.root_node_token),
                probed_at=self.clock.now(),
            )
        except RuntimeError as error:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_VERSION_CONFLICT",
                title="Tenant Provider Binding 版本冲突",
                detail="Binding 在权限探测期间已被更新。",
                repair="刷新 Binding 后重新探测。",
            ) from error

    def request_sync(
        self,
        actor: KnowledgeActor,
        project_id: str,
        request: KnowledgeSyncJobRequest,
    ) -> KnowledgeSyncJob:
        persisted, _created = self.enqueue_sync_job(
            actor.user_id,
            project_id,
            request,
            require_fresh_binding_probe=True,
            max_attempts=5,
        )
        now = self.clock.now()
        if persisted.status in {"queued", "retry_wait", "running"}:
            self.repository.recover_expired_sync_jobs(now)
            refreshed = self.get_sync_job(persisted.id)
            if refreshed.status in {"queued", "retry_wait"} and (
                refreshed.retry_at is None or refreshed.retry_at <= now
            ):
                return self.run_sync_job(refreshed.id)
            return refreshed
        return persisted

    def enqueue_sync_job(
        self,
        requested_by: str,
        project_id: str,
        request: KnowledgeSyncJobRequest,
        *,
        require_fresh_binding_probe: bool,
        max_attempts: int,
    ) -> tuple[KnowledgeSyncJob, bool]:
        binding = self._binding(request.binding_id)
        now = self.clock.now()
        if require_fresh_binding_probe and (
            binding.last_permission_probe_at is None
            or binding.last_permission_probe_at < now - timedelta(minutes=30)
        ):
            raise ProductError(
                code="KNOWLEDGE_PERMISSION_PROBE_STALE",
                title="知识来源权限探测已过期",
                detail="同步前必须确认 Tenant App 仍可读取该 Binding。",
                repair="重新 Diagnose 或刷新 Binding 权限探测后重试。",
                status_code=409,
            )
        allowed_sources = {
            node.source_id
            for node in self.repository.list_binding_nodes(binding.id)
            if node.source_id is not None
        }
        if request.source_id not in allowed_sources:
            raise ProductError(
                code="KNOWLEDGE_SOURCE_SCOPE_DENIED",
                title="知识来源不在批准范围内",
                detail="source_id 不属于 Binding 最近一次权限探测冻结的目录范围。",
                repair="从 Binding 目录选择来源，或由管理员刷新并重新批准 Scope。",
                status_code=403,
            )
        job = KnowledgeSyncJob(
            id=new_id(),
            project_id=project_id,
            binding_id=request.binding_id,
            source_id=request.source_id,
            idempotency_key=request.idempotency_key,
            status="queued",
            attempt=0,
            max_attempts=max_attempts,
            requested_by=requested_by,
            version=1,
            created_at=now,
            updated_at=now,
        )
        persisted, created = self.repository.create_sync_job(job)
        if not created and (
            persisted.binding_id != request.binding_id
            or persisted.source_id != request.source_id
        ):
            raise ProductError(
                code="KNOWLEDGE_SYNC_IDEMPOTENCY_CONFLICT",
                title="同步幂等键已用于其他请求",
                detail="同一项目中的 idempotency_key 已绑定不同知识来源。",
                repair="复用原请求参数，或生成新的幂等键。",
            )
        return persisted, created

    def get_sync_job(self, job_id: str) -> KnowledgeSyncJob:
        job = self.repository.get_sync_job(job_id)
        if job is None:
            raise ProductError(
                code="KNOWLEDGE_SYNC_JOB_NOT_FOUND",
                title="知识同步任务不存在",
                detail="指定的知识同步任务不存在或已被清理。",
                repair="刷新同步任务列表后重试。",
                status_code=404,
            )
        return job

    def list_project_sync_jobs(
        self,
        actor: KnowledgeActor,
        project_id: str,
        binding_id: str,
    ) -> tuple[KnowledgeSyncJob, ...]:
        del actor
        self._binding(binding_id)
        return self.repository.list_sync_jobs(project_id, binding_id)

    def list_project_snapshots(
        self,
        actor: KnowledgeActor,
        binding_id: str,
    ) -> tuple[TenantProviderSnapshotRecord, ...]:
        del actor
        self._binding(binding_id)
        return self.repository.list_active_snapshots(binding_id)

    def run_sync_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> KnowledgeSyncJob:
        queued = self.get_sync_job(job_id)
        if queued.status in {"succeeded", "failed", "cancelled"}:
            return queued
        now = self.clock.now()
        running = self.repository.acquire_sync_job(
            queued.id,
            expected_version=queued.version,
            lease_owner=lease_owner or f"inline:{new_id()}",
            now=now,
            lease_expires_at=now + timedelta(minutes=5),
        )
        if running is None:
            return self.get_sync_job(job_id)
        try:
            binding = self._binding(running.binding_id)
            connection = self._ready_connection(binding.connection_id)
        except ProductError as error:
            return self.repository.fail_sync_job(
                running,
                error_code=error.code,
                completed_at=self.clock.now(),
            )
        if self.provider_resolver is None or self.artifact_storage is None:
            return self.repository.fail_sync_job(
                running,
                error_code="KNOWLEDGE_SYNC_ADAPTER_UNAVAILABLE",
                completed_at=self.clock.now(),
            )
        provider = self.provider_resolver.resolve(connection)
        try:
            allowed_sources = {
                node.source_id
                for node in self.repository.list_binding_nodes(binding.id)
                if node.source_id is not None
            }
            if running.source_id not in allowed_sources:
                failed = self.repository.fail_sync_job(
                    running,
                    error_code="KNOWLEDGE_SOURCE_SCOPE_DENIED",
                    completed_at=self.clock.now(),
                )
                return failed
            provider_snapshot = provider.fetch_snapshot(running.source_id)
            artifact = self.artifact_storage.put_json(
                provider_snapshot.normalized_content,
                media_type="application/vnd.agent-team-os.knowledge-snapshot+json",
            )
            snapshot = TenantProviderSnapshotRecord(
                id=new_id(),
                binding_id=binding.id,
                source_id=provider_snapshot.source_id,
                provider_revision=provider_snapshot.provider_revision,
                content_type=provider_snapshot.content_type,
                artifact=artifact,
                normalized_text_sha256=sha256_bytes(
                    provider_snapshot.normalized_text.encode("utf-8")
                ),
                source_url=provider_snapshot.source_url,
                fetched_by_product_user_id=running.requested_by,
                fetched_at=provider_snapshot.fetched_at,
            )
            completed_at = self.clock.now()
            return self.repository.complete_sync_job(
                running,
                snapshot,
                permission_probe_at=completed_at,
                authorization_version=binding.authorization_version,
                completed_at=completed_at,
            )
        except ProviderFailure as error:
            if error.code in {
                "FEISHU_SOURCE_NOT_FOUND",
                "FEISHU_SOURCE_PERMISSION_REVOKED",
            }:
                failed_at = self.clock.now()
                self.repository.mark_source_head_status(
                    binding_id=binding.id,
                    source_id=running.source_id,
                    status=(
                        "tombstoned" if error.code == "FEISHU_SOURCE_NOT_FOUND" else "quarantined"
                    ),
                    permission_probe_at=failed_at,
                    binding_authorization_version=binding.authorization_version,
                    updated_at=failed_at,
                )
                return self.repository.fail_sync_job(
                    running,
                    error_code=error.code,
                    completed_at=self.clock.now(),
                )
            if error.code in {
                "FEISHU_PERMISSION_REVOKED",
                "FEISHU_TENANT_AUTH_FAILED",
                "KNOWLEDGE_CREDENTIAL_REFERENCE_UNRESOLVED",
            }:
                degraded_at = self.clock.now()
                degraded = connection.model_copy(
                    update={
                        "status": "degraded",
                        "authorization_version": connection.authorization_version
                        + int(connection.status != "degraded"),
                        "version": connection.version + 1,
                        "updated_at": degraded_at,
                        "last_diagnosed_at": degraded_at,
                        "last_error_code": error.code,
                    }
                )
                self.repository.update_connection(degraded, connection.version)
                return self.repository.fail_sync_job(
                    running,
                    error_code=error.code,
                    completed_at=self.clock.now(),
                )
            if (
                error.code
                in {
                    "FEISHU_RATE_LIMITED",
                    "FEISHU_TIMEOUT",
                    "FEISHU_UNAVAILABLE",
                }
                and running.attempt < running.max_attempts
            ):
                completed_at = self.clock.now()
                jitter = int(str(sha256_bytes(running.id.encode("utf-8")))[:2], 16) / 255 / 4
                retry_delay = max(
                    error.retry_after_seconds or 0,
                    min(float(2**running.attempt), 300.0) + jitter,
                )
                return self.repository.defer_sync_job(
                    running,
                    error_code=error.code,
                    retry_at=completed_at + timedelta(seconds=retry_delay),
                    updated_at=completed_at,
                )
            return self.repository.fail_sync_job(
                running,
                error_code=error.code,
                completed_at=self.clock.now(),
            )
        except RuntimeError as error:
            if str(error) != "KNOWLEDGE_PROVIDER_REVISION_HASH_CONFLICT":
                raise
            self.repository.fail_sync_job(
                running,
                error_code="KNOWLEDGE_PROVIDER_REVISION_HASH_CONFLICT",
                completed_at=self.clock.now(),
            )
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_REVISION_HASH_CONFLICT",
                title="Provider Revision 内容冲突",
                detail="相同 Provider Revision 返回了不同内容哈希，已隔离该同步结果。",
                repair="检查 Provider 一致性后以新的 Revision 重新同步。",
            ) from error

    def recover_sync_jobs(self) -> tuple[KnowledgeSyncJob, ...]:
        now = self.clock.now()
        recovered = self.repository.recover_expired_sync_jobs(now)
        return tuple(self.run_sync_job(job.id) for job in recovered)

    def recover_expired_sync_jobs(self) -> tuple[KnowledgeSyncJob, ...]:
        return self.repository.recover_expired_sync_jobs(self.clock.now())

    def _ready_connection(self, connection_id: str) -> TenantConnection:
        connection = self.repository.get_connection(connection_id)
        if connection is None:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_NOT_FOUND",
                title="Tenant Connection 不存在",
                detail="指定的 Tenant Connection 已不存在。",
                repair="刷新连接列表后重试。",
                status_code=404,
            )
        if connection.status != "ready" or self.provider_resolver is None:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_DEGRADED",
                title="Tenant Connection 未就绪",
                detail="只有最近 Diagnose 成功的连接可以读取或绑定知识空间。",
                repair="修复凭据或权限并重新 Diagnose。",
                status_code=503,
            )
        return connection

    def _binding(self, binding_id: str) -> TenantProviderBinding:
        binding = self.repository.get_binding(binding_id)
        if binding is None:
            raise ProductError(
                code="KNOWLEDGE_PROVIDER_BINDING_NOT_FOUND",
                title="Tenant Provider Binding 不存在",
                detail="指定 Binding 已不存在。",
                repair="刷新 Binding 列表后重试。",
                status_code=404,
            )
        if binding.status != "ready":
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_DEGRADED",
                title="Tenant Provider Binding 未就绪",
                detail="当前 Binding 不能读取目录。",
                repair="重新探测权限后重试。",
                status_code=503,
            )
        return binding

    @staticmethod
    def _provider_failure(error: ProviderFailure) -> ProductError:
        return ProductError(
            code=error.code,
            title="Tenant Knowledge Provider 不可用",
            detail="外部知识来源读取失败。",
            repair="检查 Tenant App 权限与飞书服务状态后重试。",
            status_code=503 if error.unavailable else 502,
        )

    @staticmethod
    def _require_administrator(actor: KnowledgeActor) -> None:
        if actor.role != Role.ADMINISTRATOR:
            raise ProductError(
                code="KNOWLEDGE_CONNECTION_PERMISSION_DENIED",
                title="Tenant Connection 权限不足",
                detail="只有 Administrator 可以管理外部 Tenant Connection。",
                repair="使用管理员账户重试。",
                status_code=403,
            )


def _filter_nodes(
    nodes: tuple[ProviderNode, ...], root_node_token: str | None
) -> tuple[ProviderNode, ...]:
    if root_node_token is None:
        return nodes
    allowed = {root_node_token}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.parent_external_id in allowed and node.external_id not in allowed:
                allowed.add(node.external_id)
                changed = True
    return tuple(node for node in nodes if node.external_id in allowed)
