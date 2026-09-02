"""Fail-closed readiness projection for the v0.5.1 Knowledge Live Gate.

Readiness never means that a Live Gate ran.  It only proves that the frozen
product facts and current runtime prerequisites are eligible to start one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .infrastructure.feishu import SystemSecretReferenceResolver
from .infrastructure.git import resolve_git_credential
from .infrastructure.knowledge import SQLiteVectorIndexAdapter
from .infrastructure.ollama import OllamaEmbeddingAdapter
from .modules.delivery.runtime_adapters import PRODUCT_RUNTIME_ADAPTER_CONTRACTS
from .modules.knowledge import (
    KNOWLEDGE_SYNC_RUNTIME_CONTRACT,
    EmbeddingQualificationSnapshot,
    SQLiteKnowledgeIndexRepository,
    SQLiteTenantKnowledgeRepository,
)
from .modules.knowledge.index_ports import EmbeddingPort, VectorIndexPort
from .modules.knowledge.provider_ports import ProviderFailure
from .modules.orchestration import SQLitePipelineRepository
from .modules.projects import SQLiteProjectRepository
from .modules.workcells import SQLiteProjectWorkcellRepository
from .readiness import (
    DependencyCheck,
    ReadinessReport,
    RuntimeReadiness,
    inspect_acwm_revision_lock,
)
from .shared.errors import ProductError
from .shared.features import FeatureFlags

EXPECTED_RELEASE_WORKCELLS = frozenset({"design", "frontend", "backend", "qa"})
EXPECTED_WORKCELL_STAGE_PATHS = frozenset(
    {
        "design-repair/design",
        "qa-preparation-repair/qa-preparation",
        "frontend-repair/frontend",
        "backend-repair/backend",
        "qa-delivery-repair/qa-delivery",
    }
)
EXPECTED_KNOWLEDGE_STAGE_PATHS = frozenset(
    {
        "requirements",
        "tasking",
        *EXPECTED_WORKCELL_STAGE_PATHS,
    }
)
EXPECTED_PLANNING_BINDING_SITES = frozenset(
    {"requirements.actor", "tasking.actor"}
)
EXPECTED_WORKCELL_BINDING_SITES = frozenset(
    f"{stage_path}.{slot}"
    for stage_path in EXPECTED_WORKCELL_STAGE_PATHS
    for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
)


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class KnowledgeLiveFacts(_ImmutableModel):
    """A read-only projection assembled from existing domain authorities."""

    database_ready: bool = False
    project_status: str | None = None
    team_status: str | None = None
    workcell_keys: tuple[str, ...] = ()
    workspace_count: int = Field(default=0, ge=0)
    external_workspace_count: int = Field(default=0, ge=0)
    ready_workspace_count: int = Field(default=0, ge=0)
    unique_repository_count: int = Field(default=0, ge=0)
    direct_fast_forward_main_count: int = Field(default=0, ge=0)
    resolvable_git_credential_count: int = Field(default=0, ge=0)
    pipeline_revision_id: str | None = None
    pipeline_binding_model: str | None = None
    release_contract: tuple[str, ...] = ()
    workcell_stage_paths: tuple[str, ...] = ()
    knowledge_context_stage_paths: tuple[str, ...] = ()
    required_knowledge_context_count: int = Field(default=0, ge=0)
    resolved_provider_binding_count: int = Field(default=0, ge=0)
    hermes_planning_binding_count: int = Field(default=0, ge=0)
    codex_workcell_binding_count: int = Field(default=0, ge=0)
    product_hermes_runtime_wired: bool = False
    product_knowledge_sync_runtime_wired: bool = False
    required_retrieval_policy_count: int = Field(default=0, ge=0)
    ready_retrieval_policy_count: int = Field(default=0, ge=0)
    approved_source_count: int = Field(default=0, ge=0)
    ready_source_count: int = Field(default=0, ge=0)
    fresh_permission_probe_count: int = Field(default=0, ge=0)
    resolvable_feishu_credential_count: int = Field(default=0, ge=0)
    active_index_count: int = Field(default=0, ge=0)
    passed_evaluation_count: int = Field(default=0, ge=0)
    qualified_ollama_model_count: int = Field(default=0, ge=0)
    verified_index_policy_count: int = Field(default=0, ge=0)
    live_ollama_model_count: int = Field(default=0, ge=0)


class KnowledgeLiveReadinessCheck(_ImmutableModel):
    name: str
    status: Literal["ready", "blocked"]
    detail: str
    repair: str | None = None


class KnowledgeLiveReadinessReport(_ImmutableModel):
    capability: Literal["feishu-knowledge-delivery-v1"] = (
        "feishu-knowledge-delivery-v1"
    )
    project_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    status: Literal["ready", "blocked"]
    execution_status: Literal["not_run"] = "not_run"
    checks: tuple[KnowledgeLiveReadinessCheck, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeLiveFactsCollector:
    """Build a cross-module read projection without taking authority from its sources."""

    def __init__(
        self,
        database: Path,
        *,
        feishu_secrets: SystemSecretReferenceResolver | None = None,
        git_credential_resolver: Callable[[str], str] = resolve_git_credential,
        embedding_port: EmbeddingPort | None = None,
        vector_index_port: VectorIndexPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.feishu_secrets = feishu_secrets or SystemSecretReferenceResolver()
        self.git_credential_resolver = git_credential_resolver
        self.embedding_port = embedding_port or OllamaEmbeddingAdapter()
        self.vector_index_port = vector_index_port or SQLiteVectorIndexAdapter()
        self.clock = clock or (lambda: datetime.now(UTC))

    def collect(self, project_id: str) -> KnowledgeLiveFacts:
        if not self._database_ready():
            return KnowledgeLiveFacts()
        try:
            return self._collect(project_id)
        except (KeyError, sqlite3.Error, ValueError):
            return KnowledgeLiveFacts(database_ready=True)

    def _database_ready(self) -> bool:
        if not self.database.is_file():
            return False
        try:
            with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as connection:
                connection.execute("SELECT version FROM schema_migrations LIMIT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

    def _collect(self, project_id: str) -> KnowledgeLiveFacts:
        projects = SQLiteProjectRepository(self.database)
        workcells = SQLiteProjectWorkcellRepository(self.database)
        pipelines = SQLitePipelineRepository(self.database)
        tenants = SQLiteTenantKnowledgeRepository(self.database)
        indexes = SQLiteKnowledgeIndexRepository(self.database)

        project = projects.get(project_id)
        project_status = None if project is None else project.lifecycle_status
        team_status: str | None = None
        with suppress(KeyError):
            team_status = workcells.get_team(project_id).status
        workcell_bindings = workcells.list_workcells(project_id)
        workspace_bindings = workcells.list_workspaces(project_id)
        external = tuple(
            workspace
            for workspace in workspace_bindings
            if workspace.adapter_type == "external-git"
        )
        ready_workspaces = tuple(
            workspace for workspace in workspace_bindings if workspace.status == "ready"
        )
        direct_fast_forward = tuple(
            workspace
            for workspace in external
            if workspace.status == "ready"
            and workspace.verification.get("direct_fast_forward_main") is True
        )
        resolvable_git = tuple(
            workspace
            for workspace in direct_fast_forward
            if workspace.credential_reference is not None
            and self._git_credential_is_resolvable(workspace.credential_reference)
        )

        pipeline_revision_id: str | None = None
        pipeline_binding_model: str | None = None
        release_contract: tuple[str, ...] = ()
        workcell_stage_paths: tuple[str, ...] = ()
        knowledge_context_stage_paths: tuple[str, ...] = ()
        required_knowledge_context_count = 0
        resolved_provider_binding_count = 0
        hermes_planning_binding_count = 0
        codex_workcell_binding_count = 0
        hermes_planning_adapter_ids: tuple[str, ...] = ()
        retrieval_policy_ids: tuple[str, ...] = ()
        default_bindings = tuple(
            binding
            for binding in projects.list_pipeline_bindings(project_id)
            if binding.enabled and binding.is_default
        )
        if len(default_bindings) == 1:
            binding = default_bindings[0]
            try:
                revision = pipelines.get_revision(
                    binding.pipeline_id, binding.pipeline_revision
                )
            except KeyError:
                revision = None
            if revision is not None:
                pipeline_revision_id = (
                    f"{revision.pipeline_id}:{revision.revision}"
                )
                pipeline_binding_model = revision.binding_model
                release_contract = tuple(sorted(revision.release_contract_snapshot))
                workcell_stage_paths = tuple(sorted(revision.workcell_stage_map))
                knowledge_context_stage_paths = tuple(
                    sorted(revision.knowledge_context_bindings)
                )
                required_knowledge_context_count = sum(
                    item.required
                    for item in revision.knowledge_context_bindings.values()
                )
                retrieval_policy_ids = tuple(
                    sorted(
                        {
                            item.retrieval_policy_revision_id
                            for item in revision.knowledge_context_bindings.values()
                        }
                    )
                )
                resolved_provider_binding_count = len(
                    revision.resolved_provider_bindings
                )
                live_hermes_bindings = tuple(
                    snapshot
                    for site, snapshot in revision.resolved_provider_bindings.items()
                    if _is_live_provider_binding(
                        site,
                        snapshot,
                        expected_sites=EXPECTED_PLANNING_BINDING_SITES,
                        provider_id="hermes-provider",
                        adapter_ids=frozenset({"hermes.acp", "http.sync"}),
                    )
                )
                hermes_planning_binding_count = len(live_hermes_bindings)
                hermes_planning_adapter_ids = tuple(
                    sorted(
                        str(snapshot["deployment"]["adapter_id"])  # type: ignore[index]
                        for snapshot in live_hermes_bindings
                    )
                )
                codex_workcell_binding_count = sum(
                    _is_live_provider_binding(
                        site,
                        snapshot,
                        expected_sites=EXPECTED_WORKCELL_BINDING_SITES,
                        provider_id="codex-cli-provider",
                        adapter_ids=frozenset({"codex.cli"}),
                    )
                    for site, snapshot in revision.resolved_provider_bindings.items()
                )

        connections = {item.id: item for item in tenants.list_connections()}
        tenant_bindings = {item.id: item for item in tenants.list_bindings()}
        approvals = tuple(
            approval
            for approval in projects.list_knowledge_source_approvals(project_id)
            if approval.enabled and approval.rag_enabled
        )
        ready_source_ids: set[str] = set()
        fresh_source_ids: set[str] = set()
        resolvable_source_ids: set[str] = set()
        freshness_threshold = self.clock() - timedelta(minutes=30)
        for approval in approvals:
            source = tenant_bindings.get(approval.binding_id)
            connection = (
                None if source is None else connections.get(source.connection_id)
            )
            if (
                source is None
                or source.status != "ready"
                or connection is None
                or connection.status != "ready"
            ):
                continue
            ready_source_ids.add(source.id)
            active_source_ids = tenants.list_active_source_ids(
                source.id,
                permission_probe_not_before=freshness_threshold,
            )
            if not active_source_ids:
                continue
            fresh_source_ids.add(source.id)
            if self._feishu_credentials_are_resolvable(
                connection.app_id_ref, connection.app_secret_ref
            ):
                resolvable_source_ids.add(source.id)

        ready_policy_ids: set[str] = set()
        active_index_ids: set[str] = set()
        passed_policy_ids: set[str] = set()
        qualified_policy_ids: set[str] = set()
        verified_index_policy_ids: set[str] = set()
        live_model_policy_ids: set[str] = set()
        live_qualification_cache: dict[str, bool] = {}
        vector_descriptor = self._vector_descriptor()
        for policy_id in retrieval_policy_ids:
            policy = indexes.get_retrieval_policy(policy_id)
            evaluation_policy = indexes.get_evaluation_policy_for_retrieval(policy_id)
            if policy is None or evaluation_policy is None:
                continue
            for source_id in sorted(resolvable_source_ids):
                active = indexes.get_active_index(
                    source_id, policy.index_profile_revision_id
                )
                if active is None or active.status != "active":
                    continue
                ready_policy_ids.add(policy_id)
                active_index_ids.add(active.id)
                evaluation_passed = indexes.has_passed_evaluation(
                    evaluation_policy.id, active.id
                )
                if evaluation_passed:
                    passed_policy_ids.add(policy_id)
                if active.embedding_qualification_id is None:
                    continue
                qualification = indexes.get_qualification(
                    active.embedding_qualification_id
                )
                if not self._qualification_is_current(qualification, vector_descriptor):
                    continue
                qualified_policy_ids.add(policy_id)
                if qualification is None or not evaluation_passed:
                    continue
                verified_index_policy_ids.add(policy_id)
                is_live = live_qualification_cache.get(qualification.id)
                if is_live is None:
                    is_live = self._ollama_qualification_is_live(qualification)
                    live_qualification_cache[qualification.id] = is_live
                if is_live:
                    live_model_policy_ids.add(policy_id)
                    break

        return KnowledgeLiveFacts(
            database_ready=True,
            project_status=project_status,
            team_status=team_status,
            workcell_keys=tuple(sorted(item.workcell_key for item in workcell_bindings)),
            workspace_count=len(workspace_bindings),
            external_workspace_count=len(external),
            ready_workspace_count=len(ready_workspaces),
            unique_repository_count=len(
                {workspace.repository_uri for workspace in workspace_bindings}
            ),
            direct_fast_forward_main_count=len(direct_fast_forward),
            resolvable_git_credential_count=len(resolvable_git),
            pipeline_revision_id=pipeline_revision_id,
            pipeline_binding_model=pipeline_binding_model,
            release_contract=release_contract,
            workcell_stage_paths=workcell_stage_paths,
            knowledge_context_stage_paths=knowledge_context_stage_paths,
            required_knowledge_context_count=required_knowledge_context_count,
            resolved_provider_binding_count=resolved_provider_binding_count,
            hermes_planning_binding_count=hermes_planning_binding_count,
            codex_workcell_binding_count=codex_workcell_binding_count,
            product_hermes_runtime_wired=_runtime_bindings_wired(
                hermes_planning_adapter_ids,
                expected_count=len(EXPECTED_PLANNING_BINDING_SITES),
            ),
            product_knowledge_sync_runtime_wired=(
                KNOWLEDGE_SYNC_RUNTIME_CONTRACT
                == {
                    "contract_id": "knowledge-sync-runtime-v1",
                    "scheduler_authority": "knowledge-sync-job-repository",
                    "poll_interval_seconds": 900,
                    "directory_reconciliation_interval_seconds": 86400,
                    "worker_concurrency": 2,
                    "max_attempts": 5,
                    "lease_seconds": 300,
                }
            ),
            required_retrieval_policy_count=len(retrieval_policy_ids),
            ready_retrieval_policy_count=len(ready_policy_ids),
            approved_source_count=len(approvals),
            ready_source_count=len(ready_source_ids),
            fresh_permission_probe_count=len(fresh_source_ids),
            resolvable_feishu_credential_count=len(resolvable_source_ids),
            active_index_count=len(active_index_ids),
            passed_evaluation_count=len(passed_policy_ids),
            qualified_ollama_model_count=len(qualified_policy_ids),
            verified_index_policy_count=len(verified_index_policy_ids),
            live_ollama_model_count=len(live_model_policy_ids),
        )

    def _git_credential_is_resolvable(self, reference: str) -> bool:
        try:
            return bool(self.git_credential_resolver(reference))
        except Exception:
            return False

    def _feishu_credentials_are_resolvable(
        self, app_id_ref: str, app_secret_ref: str
    ) -> bool:
        try:
            return bool(self.feishu_secrets.resolve(app_id_ref)) and bool(
                self.feishu_secrets.resolve(app_secret_ref)
            )
        except (ProviderFailure, OSError):
            return False

    def _vector_descriptor(self) -> tuple[str, str, str] | None:
        try:
            descriptor = self.vector_index_port.describe()
        except Exception:
            return None
        return (
            descriptor.engine_name,
            descriptor.engine_version,
            descriptor.adapter_revision,
        )

    @staticmethod
    def _qualification_is_current(
        qualification: EmbeddingQualificationSnapshot | None,
        vector_descriptor: tuple[str, str, str] | None,
    ) -> bool:
        if qualification is None or vector_descriptor is None:
            return False
        return bool(
            qualification.status == "qualified"
            and qualification.adapter_revision == "ollama-embedding-http-v1"
            and vector_descriptor
            == (
                "sqlite-vec",
                qualification.sqlite_vec_version,
                qualification.vector_index_adapter_revision,
            )
        )

    def _ollama_qualification_is_live(
        self, qualification: EmbeddingQualificationSnapshot
    ) -> bool:
        try:
            model_name = qualification.model_name
            descriptor = self.embedding_port.describe(model_name)
            vectors = self.embedding_port.embed(
                ("Agent-Team-OS knowledge readiness probe",),
                model_name=model_name,
                truncate=False,
            )
        except Exception:
            return False
        return bool(
            descriptor.model_name == model_name
            and descriptor.model_digest == qualification.model_digest
            and self.embedding_port.adapter_revision
            == qualification.adapter_revision
            and len(vectors) == 1
            and len(vectors[0]) == qualification.dimension
        )


def inspect_knowledge_live_readiness(
    *,
    project_root: Path,
    data_dir: Path,
    project_id: str,
    flags: FeatureFlags | None = None,
    runtime: ReadinessReport | None = None,
    collector: KnowledgeLiveFactsCollector | None = None,
) -> KnowledgeLiveReadinessReport:
    """Inspect current facts and adapters without starting a Delivery."""

    if flags is None:
        try:
            flags = FeatureFlags.from_environment()
        except ProductError:
            flags = FeatureFlags()
    facts = (
        collector
        or KnowledgeLiveFactsCollector(data_dir / "agent-team-os.sqlite")
    ).collect(project_id)
    return evaluate_knowledge_live_readiness(
        project_id=project_id,
        facts=facts,
        flags=flags,
        framework_revision=inspect_acwm_revision_lock(
            project_root / "config" / "framework-lock.json"
        ),
        runtime=runtime or RuntimeReadiness().inspect(),
    )


def write_knowledge_live_readiness_report(
    report_dir: Path, report: KnowledgeLiveReadinessReport
) -> tuple[Path, Path]:
    """Persist a non-secret readiness receipt distinct from Release Gate reports."""

    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%dT%H%M%SZ")
    project_token = hashlib.sha256(report.project_id.encode()).hexdigest()[:12]
    stem = f"{timestamp}-knowledge-live-readiness-{project_token}"
    json_path = report_dir / f"{stem}.json"
    markdown_path = report_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Feishu Knowledge Delivery Live Readiness",
        "",
        f"- Capability: `{report.capability}`",
        f"- Project: `{report.project_id}`",
        f"- Readiness: `{report.status}`",
        f"- Live Execution: `{report.execution_status}`",
        "",
        "| Check | Status | Detail | Repair |",
        "|---|---|---|---|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                check.name,
                check.status,
                check.detail.replace("|", "\\|"),
                (check.repair or "-").replace("|", "\\|"),
            )
        )
        + " |"
        for check in report.checks
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def evaluate_knowledge_live_readiness(
    *,
    project_id: str,
    facts: KnowledgeLiveFacts,
    flags: FeatureFlags,
    framework_revision: DependencyCheck,
    runtime: ReadinessReport,
) -> KnowledgeLiveReadinessReport:
    """Evaluate eligibility without upgrading readiness into Live evidence."""

    flags_ready = all(
        (
            flags.feishu_tenant_sync_v1,
            flags.knowledge_hybrid_index_v1,
            flags.delivery_knowledge_context_v1,
        )
    )
    checks = (
        _check(
            "data-store",
            facts.database_ready,
            "本地数据库已存在且可按当前 Migration 读取。",
            "先启动 Agent-Team-OS 完成 Migration，并确认 AGENT_TEAM_OS_DATA_DIR 指向正确数据目录。",
            blocked_detail="数据库不存在，或无法按当前 Migration 读取。",
        ),
        _check(
            "feature-flags",
            flags_ready,
            "Gate A/B/C Feature Flag 已按依赖顺序开启。",
            "按 Gate A → Gate B → Gate C 顺序开启三个 v0.5.1 Feature Flag。",
            blocked_detail="Gate A/B/C Feature Flag 尚未按依赖顺序全部开启。",
        ),
        _check(
            "framework-lock",
            framework_revision.status == "ready",
            "ACWM Stage Input Artifact Contract 与产品锁定 Revision 一致。",
            framework_revision.repair
            or "发布并锁定包含 knowledge-context-v1 Contract 的 ACWM Revision。",
            blocked_detail="ACWM Revision/Dependency Attestation 未通过。",
        ),
        _check(
            "runtime",
            runtime.status == "ready",
            "AgentScope、Hermes 和 Codex Runtime 身份就绪。",
            _runtime_repair(runtime),
            blocked_detail="AgentScope、Hermes 或 Codex Runtime Readiness 尚未通过。",
        ),
        _check(
            "project-governance",
            facts.project_status == "active"
            and facts.team_status == "active"
            and set(facts.workcell_keys) == EXPECTED_RELEASE_WORKCELLS,
            "Project 与四 Workcell Team Binding 处于活动状态。",
            "激活项目、TeamTemplate Revision 以及 design/frontend/backend/qa 组织绑定。",
            blocked_detail=(
                "Project、TeamTemplate Revision 或 design/frontend/backend/qa "
                "Workcell Binding 尚未全部活动。"
            ),
        ),
        _check(
            "external-git-workspaces",
            _external_workspaces_ready(facts),
            "四个独立 GitHub HTTPS Workspace 均已验证直接 Fast-forward main 权限。",
            "绑定四个互不相同的 external-git Workspace，解析凭据并重新执行 Workspace Verify。",
            blocked_detail=(
                "四个独立 external-git Workspace、凭据解析或直接 Fast-forward main 权限"
                "尚未全部验证。"
            ),
        ),
        _check(
            "published-knowledge-pipeline",
            _pipeline_ready(facts),
            (
                "Published provider-v1 Pipeline 冻结了四仓 Release Contract "
                "和七个 Knowledge Context Slot。"
            ),
            (
                "发布并绑定含完整 Workcell Stage Map、Release Contract 和 "
                "KnowledgeContextBinding 的 Pipeline Revision。"
            ),
            blocked_detail=(
                "Published provider-v1 Pipeline 缺少完整四仓 Release Contract、"
                "Workcell Stage Map 或七个 Knowledge Context Slot。"
            ),
        ),
        _check(
            "live-provider-bindings",
            _live_provider_bindings_ready(facts),
            "Published Pipeline 以真实 Hermes 规划和 Codex Workcell 身份冻结全部 Slot。",
            (
                "将 requirements/tasking 绑定到 hermes-provider，将五个 Workcell 的 "
                "20 个 Slot 绑定到 codex-cli-provider，重新资格化并发布 Pipeline。"
            ),
            blocked_detail=(
                "Published Pipeline 尚未以真实 Hermes 规划和 Codex Workcell 身份"
                "冻结全部 Slot。"
            ),
        ),
        _check(
            "product-runtime-adapters",
            facts.product_hermes_runtime_wired,
            "产品 Runtime Dispatcher 已接线 Hermes role-turn Adapter。",
            (
                "将 requirements/tasking 的 Published Binding 冻结为产品已接线的 "
                "hermes.acp role-turn Adapter，并验证 Runtime Instance、Identity 与连接指纹；"
                "仅安装 CLI/ACWM Adapter 不等于产品已接线。"
            ),
            blocked_detail=(
                "Published Planning Binding 尚未选择产品已接线的 Hermes role-turn Adapter，"
                "或实例/配置验证未通过。"
            ),
        ),
        _check(
            "knowledge-sync-runtime",
            facts.product_knowledge_sync_runtime_wired,
            (
                "产品已接线持久化 Scheduler/Worker：15 分钟轮询、24 小时目录对账、"
                "并发 2、最多 5 次尝试。"
            ),
            (
                "接入 knowledge-sync-runtime-v1，并保持 KnowledgeSyncJob Repository、"
                "Lease、重启恢复和 Source 级权限新鲜度为状态权威。"
            ),
            blocked_detail=(
                "持久化 Knowledge Sync Scheduler/Worker 尚未完成运行时接线或验证。"
            ),
        ),
        _check(
            "feishu-approved-source",
            _source_ready(facts),
            "存在凭据可解析、Source 权限探测新鲜且项目已批准 RAG 的 Feishu Source。",
            (
                "诊断 Tenant Connection/Binding，完成 Source 同步以刷新 30 分钟权限证据，"
                "并为项目批准该 Source 用于 RAG。"
            ),
            blocked_detail=(
                "没有同时满足凭据可解析、权限探测新鲜且项目已批准 RAG 的 Feishu Source。"
            ),
        ),
        _check(
            "qualified-hybrid-index",
            _index_ready(facts),
            "项目已批准 Source 存在 Active 且通过 Published Evaluation Policy 的 Hybrid Index。",
            "使用真实 Snapshot 构建 Shadow Index，通过 Retrieval Evaluation 后 CAS 激活。",
            blocked_detail=(
                "项目已批准 Source 尚无通过 Published Evaluation Policy 并激活的 Hybrid Index。"
            ),
        ),
        _check(
            "ollama-model",
            facts.required_retrieval_policy_count > 0
            and facts.live_ollama_model_count
            == facts.required_retrieval_policy_count,
            "当前 Ollama 模型名、Digest、维度和 Adapter Revision 与资格快照一致。",
            "启动 Ollama，安装锁定 bge-m3，并重新执行 Embedding Qualification。",
            blocked_detail=(
                "当前 Ollama 模型名、Digest、维度或 Adapter Revision 与资格快照不一致。"
            ),
        ),
    )
    return KnowledgeLiveReadinessReport(
        project_id=project_id,
        status="ready" if all(item.status == "ready" for item in checks) else "blocked",
        checks=checks,
    )


def _check(
    name: str,
    ready: bool,
    ready_detail: str,
    repair: str | None,
    *,
    blocked_detail: str,
) -> KnowledgeLiveReadinessCheck:
    return KnowledgeLiveReadinessCheck(
        name=name,
        status="ready" if ready else "blocked",
        detail=ready_detail if ready else blocked_detail,
        repair=None if ready else repair,
    )


def _runtime_repair(runtime: ReadinessReport) -> str:
    missing = tuple(check.name for check in runtime.checks if check.status != "ready")
    return (
        "修复 Runtime Readiness：" + "、".join(missing)
        if missing
        else "重新执行 Runtime Readiness 诊断。"
    )


def _external_workspaces_ready(facts: KnowledgeLiveFacts) -> bool:
    expected = len(EXPECTED_RELEASE_WORKCELLS)
    return all(
        count == expected
        for count in (
            facts.workspace_count,
            facts.external_workspace_count,
            facts.ready_workspace_count,
            facts.unique_repository_count,
            facts.direct_fast_forward_main_count,
            facts.resolvable_git_credential_count,
        )
    )


def _pipeline_ready(facts: KnowledgeLiveFacts) -> bool:
    return (
        facts.pipeline_revision_id is not None
        and facts.pipeline_binding_model == "provider-v1"
        and set(facts.release_contract) == EXPECTED_RELEASE_WORKCELLS
        and set(facts.workcell_stage_paths) == EXPECTED_WORKCELL_STAGE_PATHS
        and set(facts.knowledge_context_stage_paths) == EXPECTED_KNOWLEDGE_STAGE_PATHS
        and facts.required_knowledge_context_count == len(EXPECTED_KNOWLEDGE_STAGE_PATHS)
    )


def _source_ready(facts: KnowledgeLiveFacts) -> bool:
    return all(
        count > 0
        for count in (
            facts.approved_source_count,
            facts.ready_source_count,
            facts.fresh_permission_probe_count,
            facts.resolvable_feishu_credential_count,
        )
    )


def _live_provider_bindings_ready(facts: KnowledgeLiveFacts) -> bool:
    return (
        facts.resolved_provider_binding_count
        == len(EXPECTED_PLANNING_BINDING_SITES)
        + len(EXPECTED_WORKCELL_BINDING_SITES)
        and facts.hermes_planning_binding_count
        == len(EXPECTED_PLANNING_BINDING_SITES)
        and facts.codex_workcell_binding_count
        == len(EXPECTED_WORKCELL_BINDING_SITES)
    )


def _runtime_bindings_wired(
    adapter_ids: tuple[str, ...], *, expected_count: int
) -> bool:
    return len(adapter_ids) == expected_count and all(
        (adapter_id, "agentscope.role-turn") in PRODUCT_RUNTIME_ADAPTER_CONTRACTS
        for adapter_id in adapter_ids
    )


def _is_live_provider_binding(
    site: str,
    snapshot: dict[str, object],
    *,
    expected_sites: frozenset[str],
    provider_id: str,
    adapter_ids: frozenset[str],
) -> bool:
    if site not in expected_sites:
        return False
    deployment = snapshot.get("deployment")
    runtime_identity = snapshot.get("runtime_identity")
    if not isinstance(deployment, dict) or not isinstance(runtime_identity, str):
        return False
    normalized_identity = runtime_identity.lower()
    return bool(
        deployment.get("provider_id") == provider_id
        and deployment.get("adapter_id") in adapter_ids
        and "deterministic" not in normalized_identity
        and "simulated" not in normalized_identity
    )


def _index_ready(facts: KnowledgeLiveFacts) -> bool:
    return (
        facts.required_retrieval_policy_count > 0
        and facts.ready_retrieval_policy_count
        == facts.required_retrieval_policy_count
        and facts.active_index_count > 0
        and all(
            count == facts.required_retrieval_policy_count
            for count in (
                facts.passed_evaluation_count,
                facts.qualified_ollama_model_count,
                facts.verified_index_policy_count,
            )
        )
    )
