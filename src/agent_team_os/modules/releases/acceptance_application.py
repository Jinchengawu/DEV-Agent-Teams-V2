from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from ...delivery import DeliveryRun, SQLiteDeliveryRepository
from ...knowledge_context_contract import KNOWLEDGE_CONTEXT_STAGE_PATHS
from ...readiness import snapshot_delivery_build_identity
from ...shared.hashes import Sha256, sha256_bytes, sha256_json
from ..agents import AgentRun, AgentRunLedger
from ..artifacts import ContentAddressedArtifactStorage
from ..knowledge import KnowledgeAuthorizationStampV1, SQLiteKnowledgeContextRepository
from ..orchestration import (
    SQLitePipelineRepository,
    SQLitePipelineRunRepository,
    WorkcellStageBinding,
)
from ..workcells import (
    AgentAttempt,
    CandidateVerification,
    FrozenSlotBinding,
    ReviewArtifact,
    SQLiteWorkcellExecutionRepository,
    WorkcellDefinition,
    WorkcellResult,
    WorkcellResultValidation,
    WorkcellRunTree,
)
from .acceptance_domain import (
    ReleaseAcceptanceCheckV2,
    ReleaseAcceptanceReportV2,
)
from .v2_domain import (
    GitHubPRReceipt,
    ReleaseBundleV2,
    ReleaseManifestV2,
    RemoteApplyReceipt,
    WorkspaceCandidateV2,
)
from .v2_repository import SQLiteExternalReleaseRepository

_TERMINAL_WORKCELL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
)
_PLANNING_BINDING_SITES = ("requirements.actor", "tasking.actor")


class RemoteRevisionPort(Protocol):
    def revision(self, candidate: WorkspaceCandidateV2) -> str: ...


class KnowledgeAcceptanceGuard(Protocol):
    def admit(self, delivery: object, stage_path: str) -> object | None: ...

    def validate_citations(
        self,
        delivery: object,
        stage_path: str,
        citation_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class ReleaseAcceptanceVerifierV2:
    """Read-only verifier for one completed V2 Delivery.

    The public Interface is intentionally narrow.  All cross-module joins stay
    inside this deep Module and never acquire Apply or execution authority.
    """

    def __init__(
        self,
        *,
        database: Path,
        project_root: Path,
        artifact_root: Path,
        remote: RemoteRevisionPort | None,
        knowledge_guard: KnowledgeAcceptanceGuard | None,
        clock: Callable[[], datetime] | None = None,
        current_build_identity: Callable[[], object] | None = None,
    ) -> None:
        self.database = database
        self.project_root = project_root
        self.deliveries = SQLiteDeliveryRepository(database)
        self.pipeline_runs = SQLitePipelineRunRepository(database)
        self.pipelines = SQLitePipelineRepository(database)
        self.agent_runs = AgentRunLedger(
            database,
            artifact_storage=ContentAddressedArtifactStorage(artifact_root),
        )
        self.workcells = SQLiteWorkcellExecutionRepository(database)
        self.knowledge = SQLiteKnowledgeContextRepository(database)
        self.releases = SQLiteExternalReleaseRepository(database)
        self.artifacts = ContentAddressedArtifactStorage(artifact_root)
        self.remote = remote
        self.knowledge_guard = knowledge_guard
        self.clock = clock or (lambda: datetime.now(UTC))
        self.current_build_identity = current_build_identity or (
            lambda: snapshot_delivery_build_identity(project_root)
        )

    def verify(self, *, project_id: str, delivery_id: str) -> ReleaseAcceptanceReportV2:
        delivery = self.deliveries.get(delivery_id)
        subject_ok = delivery is not None and delivery.project_id == project_id
        checks: list[ReleaseAcceptanceCheckV2] = [
            _check(
                "ACCEPTANCE_SUBJECT_VERIFIED",
                subject_ok,
                passed_detail="Delivery 与 Project 身份已绑定。",
                failed_detail="Delivery 不存在或不属于指定 Project。",
                evidence={
                    "project_id": project_id,
                    "delivery_id": delivery_id,
                    "subject_exists": delivery is not None,
                    "project_matches": subject_ok,
                },
            )
        ]
        if delivery is None or not subject_ok:
            return ReleaseAcceptanceReportV2.create(
                project_id=project_id,
                delivery_id=delivery_id,
                checks=tuple(checks),
                created_at=self.clock(),
            )

        snapshot = delivery.delivery_execution_snapshot
        build_ok, current_build = self._verify_build_identity(delivery)
        checks.append(
            _check(
                "BUILD_IDENTITY_VERIFIED",
                build_ok,
                passed_detail="产品与 ACWM 的干净 Revision、Lock 和依赖解析均与执行快照一致。",
                failed_detail="Build Identity 缺失、Dirty 或与当前 Revision/依赖解析不一致。",
                evidence={
                    "delivery_id": delivery.id,
                    "frozen": None if snapshot is None else snapshot.build_identity,
                    "current": current_build,
                },
            )
        )

        snapshot_hash_ok = snapshot is not None and snapshot.snapshot_sha256 == sha256_json(
            snapshot.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        delivery_ok = delivery.status == "completed" and snapshot is not None and snapshot_hash_ok
        checks.append(
            _check(
                "DELIVERY_TERMINAL_VERIFIED",
                delivery_ok,
                passed_detail="Delivery 已进入 completed 终态并保留 V2 执行快照。",
                failed_detail="Delivery 尚未完成或缺少 V2 执行快照。",
                evidence={
                    "delivery_id": delivery.id,
                    "status": delivery.status,
                    "snapshot_sha256": None if snapshot is None else snapshot.snapshot_sha256,
                },
            )
        )

        pipeline_ok, pipeline_fingerprint = self._verify_pipeline(delivery)
        checks.append(
            _check(
                "PIPELINE_TERMINAL_VERIFIED",
                pipeline_ok,
                passed_detail="Pipeline Run 已完成且 Revision/Fingerprint 与 Delivery 快照一致。",
                failed_detail="Pipeline Run 缺失、未完成或与 Delivery 快照漂移。",
                evidence={
                    "delivery_id": delivery.id,
                    "pipeline_revision_id": delivery.pipeline_revision_id,
                    "pipeline_fingerprint": pipeline_fingerprint,
                },
            )
        )

        planning_contract = _planning_runtime_contract(
            {} if snapshot is None else snapshot.resolved_provider_bindings
        )
        planning_code, expected_planning_adapter, planning_name = (
            planning_contract
            if planning_contract is not None
            else ("PLANNING_ATTEMPTS_VERIFIED", "", "Planning")
        )
        planning_ok = bool(expected_planning_adapter) and self._verify_planning_runtime(
            delivery,
            expected_adapter=expected_planning_adapter,
        )
        checks.append(
            _check(
                planning_code,
                planning_ok,
                passed_detail=(
                    f"Requirements/Tasking 的 {planning_name} AgentRun 与 AgentAttempt "
                    "均匹配冻结 Binding。"
                ),
                failed_detail=(
                    f"{planning_name} Run/Attempt 缺失、失败、模拟化或 Binding 已漂移。"
                ),
                evidence={"delivery_id": delivery.id, "planning_verified": planning_ok},
            )
        )

        trees, final_trees = self._workcell_trees(delivery)
        workcell_state_ok = self._verify_workcell_state(delivery, trees, final_trees)
        checks.append(
            _check(
                "WORKCELL_TERMINALS_VERIFIED",
                workcell_state_ok,
                passed_detail=(
                    "所有 WorkcellRun 均已终止，且每个必需 Stage 的最终 Loop Iteration 成功。"
                ),
                failed_detail="存在未终止 WorkcellRun，或必需 Stage 的最终 Loop Iteration 未成功。",
                evidence=_workcell_state_evidence(delivery, trees, final_trees),
            )
        )

        workcell_runtime_ok = self._verify_workcell_runtime(final_trees)
        workcell_results_ok = self._verify_workcell_results(final_trees)
        workcell_evidence_sha256 = (
            _workcell_evidence_sha256(final_trees)
            if (final_trees and workcell_state_ok and workcell_runtime_ok and workcell_results_ok)
            else None
        )
        checks.append(
            _check(
                "CODEX_WORKCELL_ATTEMPTS_VERIFIED",
                workcell_runtime_ok,
                passed_detail=(
                    "最终 Workcell 的 Main/Child/Attempt 均成功并匹配冻结 Codex Slot Binding。"
                ),
                failed_detail=(
                    "最终 Workcell 的 Agent/Attempt 缺失、失败、模拟化或 Binding 已漂移。"
                ),
                evidence={
                    "delivery_id": delivery.id,
                    "workcell_evidence_sha256": workcell_evidence_sha256,
                    "runtime_verified": workcell_runtime_ok,
                },
            )
        )
        checks.append(
            _check(
                "WORKCELL_RESULTS_VERIFIED",
                workcell_results_ok,
                passed_detail=(
                    "最终 WorkcellResult、Candidate Verification 或 Artifact-only "
                    "Validation 及输出 Artifact 均通过内容完整性校验。"
                ),
                failed_detail=(
                    "最终 WorkcellResult、Verification/Validation 或输出 Artifact "
                    "缺失、失败或 Hash 不一致。"
                ),
                evidence={
                    "delivery_id": delivery.id,
                    "workcell_evidence_sha256": workcell_evidence_sha256,
                    "results_verified": workcell_results_ok,
                },
            )
        )

        knowledge_ok, knowledge_context_set_sha256 = self._verify_knowledge(delivery, final_trees)
        checks.append(
            _check(
                "KNOWLEDGE_CONTEXTS_VERIFIED",
                knowledge_ok,
                passed_detail="必需 Context、Citation 与当前授权 Epoch 均通过重新验证。",
                failed_detail="Knowledge Context/Citation 缺失、Artifact 无效或授权已经漂移。",
                evidence={
                    "delivery_id": delivery.id,
                    "knowledge_context_set_sha256": knowledge_context_set_sha256,
                    "knowledge_verified": knowledge_ok,
                },
            )
        )

        candidate_ok, candidates = self._verify_candidates(delivery, final_trees)
        checks.append(
            _check(
                "CANDIDATE_EVIDENCE_VERIFIED",
                candidate_ok,
                passed_detail=(
                    "四仓 Candidate、机器 Verification、零 Blocking Review 与 PR Receipt 一致。"
                ),
                failed_detail=(
                    "Candidate 集、Verification、Review、PR 或其内容 Hash 不完整或不一致。"
                ),
                evidence={
                    "delivery_id": delivery.id,
                    "candidate_evidence": [item.evidence_sha256 for item in candidates],
                    "candidate_verified": candidate_ok,
                },
            )
        )

        bundle_ok, bundle = self._verify_bundle(delivery, candidates)
        checks.append(
            _check(
                "RELEASE_BUNDLE_VERIFIED",
                bundle_ok,
                passed_detail=(
                    "ReleaseBundleV2 与冻结 Release Contract、Candidate/PR Evidence 一致。"
                ),
                failed_detail="ReleaseBundleV2 缺失、Hash 错误或内容发生漂移。",
                evidence={
                    "delivery_id": delivery.id,
                    "bundle_sha256": None if bundle is None else bundle.bundle_sha256,
                    "bundle_verified": bundle_ok,
                },
            )
        )

        remote_ok, receipts = self._verify_remote_apply(delivery, bundle)
        checks.append(
            _check(
                "REMOTE_MAIN_VERIFIED",
                remote_ok,
                passed_detail=(
                    "四仓 Apply Receipt 完整，当前远端 main 均精确等于获批 Candidate SHA。"
                ),
                failed_detail=(
                    "Apply Attempt/Receipt 不完整，或当前远端 main 不等于 Candidate SHA。"
                ),
                evidence={
                    "delivery_id": delivery.id,
                    "receipt_sha256": [item.receipt_sha256 for item in receipts],
                    "remote_verified": remote_ok,
                },
            )
        )

        manifest_ok, manifest = self._verify_manifest(delivery, bundle, receipts)
        checks.append(
            _check(
                "RELEASE_MANIFEST_VERIFIED",
                manifest_ok,
                passed_detail=(
                    "Active ReleaseManifestV2 与 Bundle、Receipt 及 Delivery 引用完全一致。"
                ),
                failed_detail=(
                    "ReleaseManifestV2 缺失、Hash 错误或与 Bundle/Receipt/Delivery 漂移。"
                ),
                evidence={
                    "delivery_id": delivery.id,
                    "manifest_sha256": (None if manifest is None else manifest.manifest_sha256),
                    "manifest_verified": manifest_ok,
                },
            )
        )

        health_ok = self._verify_release_health(delivery, bundle)
        checks.append(
            _check(
                "RELEASE_HEALTH_VERIFIED",
                health_ok,
                passed_detail="Project Release Health 为 healthy，且指向同一 Delivery 与 Bundle。",
                failed_detail="Project Release Health 非 healthy 或指向其他 Release。",
                evidence={"delivery_id": delivery.id, "health_verified": health_ok},
            )
        )

        frozen_build = None if snapshot is None else snapshot.build_identity
        return ReleaseAcceptanceReportV2.create(
            project_id=project_id,
            delivery_id=delivery_id,
            checks=tuple(checks),
            product_revision=(None if frozen_build is None else frozen_build.product_revision),
            acwm_revision=None if frozen_build is None else frozen_build.acwm_revision,
            pipeline_revision_id=(None if snapshot is None else snapshot.pipeline_revision_id),
            build_identity_sha256=(None if frozen_build is None else frozen_build.snapshot_sha256),
            knowledge_context_set_sha256=knowledge_context_set_sha256,
            workcell_evidence_sha256=workcell_evidence_sha256,
            release_bundle_sha256=None if bundle is None else bundle.bundle_sha256,
            release_manifest_sha256=(None if manifest is None else manifest.manifest_sha256),
            created_at=self.clock(),
        )

    def _verify_build_identity(self, delivery: DeliveryRun) -> tuple[bool, object | None]:
        snapshot = delivery.delivery_execution_snapshot
        frozen = None if snapshot is None else snapshot.build_identity
        try:
            current = self.current_build_identity()
        except Exception:
            return False, None
        current_sha = getattr(current, "snapshot_sha256", None)
        current_clean = getattr(current, "product_worktree_clean", False)
        current_dependency = getattr(current, "framework_dependency_status", None)
        return (
            frozen is not None
            and frozen.product_worktree_clean
            and frozen.framework_dependency_status == "ready"
            and frozen.snapshot_sha256
            == sha256_json(frozen.model_dump(mode="json", exclude={"snapshot_sha256"}))
            and current_clean is True
            and current_dependency == "ready"
            and current_sha == frozen.snapshot_sha256
        ), current

    def _verify_pipeline(self, delivery: DeliveryRun) -> tuple[bool, str | None]:
        snapshot = delivery.delivery_execution_snapshot
        if snapshot is None:
            return False, None
        try:
            run = self.pipeline_runs.get_for_delivery(delivery.id)
            pipeline_id, revision_text = run.pipeline_revision_id.rsplit(":", 1)
            revision = self.pipelines.get_revision(pipeline_id, int(revision_text))
        except (KeyError, ValueError):
            return False, None
        expected_revision = snapshot.pipeline_revision_id
        expected_sha = snapshot.pipeline_revision_sha256
        return (
            run.status == "completed"
            and run.pipeline_revision_id == expected_revision
            and run.graph_fingerprint == expected_sha
            and delivery.pipeline_run_id == run.id
            and delivery.pipeline_revision_id == run.pipeline_revision_id
            and delivery.resolved_pipeline_sha256 == run.graph_fingerprint
            and revision.fingerprint == expected_sha
            and revision.resolved_provider_bindings == snapshot.resolved_provider_bindings
            and {
                key: value.model_dump(mode="json")
                for key, value in revision.workcell_stage_map.items()
            }
            == snapshot.workcell_stage_map
            and revision.release_contract_snapshot == snapshot.release_contract_snapshot
            and {
                key: value.model_dump(mode="json")
                for key, value in revision.knowledge_context_bindings.items()
            }
            == snapshot.knowledge_context_bindings
        ), run.graph_fingerprint

    def _verify_planning_runtime(
        self,
        delivery: DeliveryRun,
        *,
        expected_adapter: str,
    ) -> bool:
        snapshot = delivery.delivery_execution_snapshot
        if snapshot is None:
            return False
        runs = tuple(
            run
            for run in self.agent_runs.list(delivery.id)
            if run.binding_site in _PLANNING_BINDING_SITES
        )
        attempts_by_run: dict[str, list[AgentAttempt]] = {}
        for attempt in self.workcells.list_delivery_attempts(delivery.id):
            attempts_by_run.setdefault(attempt.agent_run_id, []).append(attempt)
        if len(runs) != len(_PLANNING_BINDING_SITES):
            return False
        by_site = {run.binding_site: run for run in runs}
        if set(by_site) != set(_PLANNING_BINDING_SITES):
            return False
        try:
            for site in _PLANNING_BINDING_SITES:
                run = by_site[site]
                attempts = attempts_by_run.get(run.id, [])
                frozen = snapshot.resolved_provider_bindings.get(site)
                if (
                    len(attempts) != 1
                    or attempts[0].id != run.attempt_id
                    or attempts[0].phase != "legacy"
                    or attempts[0].ordinal != 1
                    or attempts[0].finished_at is None
                    or attempts[0].error_code is not None
                    or run.delivery_id != delivery.id
                    or run.pipeline_revision_id != snapshot.pipeline_revision_id
                    or run.binding_site != site
                    or run.workcell_run_id is not None
                    or run.parent_agent_run_id is not None
                    or run.root_agent_run_id != run.id
                    or run.depth != 0
                    or run.run_role != "main"
                    or run.slot_key is not None
                    or run.workspace_access != "legacy"
                    or run.delegate_purpose is not None
                    or not isinstance(frozen, dict)
                    or run.deployment_snapshot != frozen.get("deployment")
                    or not _run_matches_frozen_binding(
                        run,
                        attempts[0],
                        frozen,
                        expected_adapter=expected_adapter,
                    )
                    or not run.artifact_envelopes
                    or not self._artifact_envelopes_are_valid(run)
                ):
                    return False
        except Exception:
            return False
        return True

    def _workcell_trees(
        self, delivery: DeliveryRun
    ) -> tuple[tuple[WorkcellRunTree, ...], tuple[WorkcellRunTree, ...]]:
        trees = tuple(self._tree(item.id) for item in self.workcells.list_delivery(delivery.id))
        snapshot = delivery.delivery_execution_snapshot
        if snapshot is None:
            return trees, ()
        final: list[WorkcellRunTree] = []
        for stage_path in snapshot.workcell_stage_map:
            stage = tuple(item for item in trees if item.workcell_run.stage_path == stage_path)
            if not stage:
                continue
            max_iteration = max(item.workcell_run.loop_iteration for item in stage)
            latest = tuple(
                item for item in stage if item.workcell_run.loop_iteration == max_iteration
            )
            if len(latest) == 1:
                final.append(latest[0])
        return trees, tuple(final)

    def _tree(self, run_id: str) -> WorkcellRunTree:
        return WorkcellRunTree(
            workcell_run=self.workcells.get(run_id),
            delegation_plan=self.workcells.get_plan(run_id),
            agent_runs=self.workcells.list_agents(run_id),
            attempts=self.workcells.list_attempts(run_id),
            verification=self.workcells.get_verification(run_id),
            result_validation=self.workcells.get_result_validation(run_id),
            reviews=self.workcells.list_reviews(run_id),
            result=self.workcells.get_result(run_id),
        )

    def _verify_workcell_state(
        self,
        delivery: DeliveryRun,
        trees: tuple[WorkcellRunTree, ...],
        final_trees: tuple[WorkcellRunTree, ...],
    ) -> bool:
        snapshot = delivery.delivery_execution_snapshot
        if snapshot is None:
            return False
        expected = set(snapshot.workcell_stage_map)
        final_paths = {item.workcell_run.stage_path for item in final_trees}
        return (
            bool(trees)
            and all(item.workcell_run.status in _TERMINAL_WORKCELL_STATUSES for item in trees)
            and final_paths == expected
            and len(final_trees) == len(expected)
            and all(item.workcell_run.status == "succeeded" for item in final_trees)
            and all(
                item.workcell_run.workcell_snapshot_sha256
                == sha256_json(item.workcell_run.workcell_snapshot.model_dump(mode="json"))
                for item in trees
            )
            and all(self._workcell_snapshot_matches_delivery(delivery, item) for item in trees)
        )

    def _workcell_snapshot_matches_delivery(
        self,
        delivery: DeliveryRun,
        tree: WorkcellRunTree,
    ) -> bool:
        """Bind a self-consistent Workcell snapshot back to its Delivery snapshot."""

        delivery_snapshot = delivery.delivery_execution_snapshot
        run = tree.workcell_run
        workcell = run.workcell_snapshot
        if delivery_snapshot is None:
            return False
        try:
            stage = WorkcellStageBinding.model_validate(
                delivery_snapshot.workcell_stage_map[run.stage_path]
            )
            definition = WorkcellDefinition.model_validate(
                delivery_snapshot.team_workcells[run.workcell_key]
            )
        except (KeyError, ValueError):
            return False
        workspaces = tuple(
            item for item in delivery_snapshot.workspaces if item.workcell_key == run.workcell_key
        )
        if len(workspaces) != 1:
            return False
        delivery_workspace = workspaces[0]
        if (
            run.delivery_id != delivery.id
            or run.pipeline_run_id != delivery.pipeline_run_id
            or run.stage_path != workcell.stage_path
            or run.workcell_key != workcell.workcell_key
            or stage.workcell_key != run.workcell_key
            or workcell.team_template_revision_id != delivery_snapshot.team_template_revision_id
            or workcell.team_template_sha256 != delivery_snapshot.team_template_sha256
            or workcell.pipeline_revision_id != delivery_snapshot.pipeline_revision_id
            or workcell.pipeline_revision_sha256 != delivery_snapshot.pipeline_revision_sha256
            or workcell.workspace.workspace_binding_id != delivery_workspace.workspace_binding_id
            or workcell.workspace.kind != delivery_workspace.kind
            or workcell.workspace.adapter_type != delivery_workspace.adapter_type
            or workcell.workspace.repository_uri != delivery_workspace.repository_uri
            or workcell.workspace.base_revision != delivery_workspace.base_revision
            or workcell.workspace.verification_sha256 != delivery_workspace.verification_sha256
            or workcell.delegation_policy != definition.delegation_policy
            or workcell.slot_method_bindings != stage.delegate_methods
            or workcell.slot_purpose_bindings != stage.delegate_purposes
            or workcell.method_snapshot_sha256
            != delivery_snapshot.method_snapshot.qualification_sha256
        ):
            return False
        frozen_by_slot = {item.slot_key: item for item in workcell.slot_bindings}
        if len(frozen_by_slot) != len(workcell.slot_bindings) or set(frozen_by_slot) != set(
            stage.slot_bindings
        ):
            return False
        for slot_key, binding_site in stage.slot_bindings.items():
            frozen = frozen_by_slot.get(slot_key)
            provider = delivery_snapshot.resolved_provider_bindings.get(binding_site)
            if frozen is None or not isinstance(provider, dict):
                return False
            deployment = provider.get("deployment")
            binding = provider.get("binding")
            if not isinstance(deployment, dict) or not isinstance(binding, dict):
                return False
            if (
                frozen.deployment_snapshot != provider
                or frozen.deployment_id != deployment.get("id")
                or frozen.resolved_provider_binding_hash != binding.get("binding_fingerprint")
            ):
                return False
        required_references = []
        context = delivery_snapshot.knowledge_contexts.get(run.stage_path)
        if context is not None:
            required_references.append(context.artifact_reference)
        unavailable = delivery_snapshot.knowledge_context_unavailable.get(run.stage_path)
        if unavailable is not None:
            required_references.append(unavailable.receipt_reference)
        if not all(reference in workcell.input_artifacts for reference in required_references):
            return False
        try:
            for reference in workcell.input_artifacts:
                self.artifacts.get_bytes(reference)
        except Exception:
            return False
        return True

    def _verify_workcell_runtime(
        self,
        final_trees: tuple[WorkcellRunTree, ...],
    ) -> bool:
        if not final_trees:
            return False
        try:
            for tree in final_trees:
                run = tree.workcell_run
                snapshot = run.workcell_snapshot
                plan = tree.delegation_plan
                if plan is None or run.main_agent_run_id is None:
                    return False
                plan_payload = {
                    "workcell_run_id": run.id,
                    "main_agent_run_id": run.main_agent_run_id,
                    "assignments": [item.model_dump(mode="json") for item in plan.assignments],
                }
                if (
                    plan.workcell_run_id != run.id
                    or plan.main_agent_run_id != run.main_agent_run_id
                    or plan.sha256 != sha256_json(plan_payload)
                ):
                    return False
                bindings = {item.slot_key: item for item in snapshot.slot_bindings}
                assignments = {item.slot_key: item for item in plan.assignments}
                if (
                    len(bindings) != len(snapshot.slot_bindings)
                    or len(assignments) != len(plan.assignments)
                    or not set(assignments).issubset(set(bindings) - {"main"})
                    or len(assignments) > snapshot.delegation_policy.max_children
                    or sum(item.workspace_access == "workspace_write" for item in plan.assignments)
                    > snapshot.delegation_policy.max_writers
                ):
                    return False
                for slot_key, assignment in assignments.items():
                    if (
                        assignment.method_id != snapshot.slot_method_bindings.get(slot_key)
                        or assignment.delegate_purpose
                        != snapshot.slot_purpose_bindings.get(slot_key)
                        or any(
                            reference not in snapshot.input_artifacts
                            for reference in assignment.input_artifacts
                        )
                    ):
                        return False
                    for reference in assignment.input_artifacts:
                        self.artifacts.get_bytes(reference)

                mains = tuple(item for item in tree.agent_runs if item.run_role == "main")
                children = tuple(item for item in tree.agent_runs if item.run_role == "child")
                if (
                    len(mains) != 1
                    or len(children) != len(assignments)
                    or len(tree.agent_runs) != 1 + len(children)
                ):
                    return False
                main = mains[0]
                children_by_slot = {item.slot_key: item for item in children}
                if (
                    len(children_by_slot) != len(children)
                    or set(children_by_slot) != set(assignments)
                    or main.id != run.main_agent_run_id
                    or main.delivery_id != run.delivery_id
                    or main.pipeline_revision_id != snapshot.pipeline_revision_id
                    or main.workcell_run_id != run.id
                    or main.binding_site != f"{run.stage_path}:main"
                    or main.parent_agent_run_id is not None
                    or main.root_agent_run_id != main.id
                    or main.depth != 0
                    or main.slot_key != "main"
                    or main.workspace_access != "none"
                    or main.delegate_purpose is not None
                    or main.status != "succeeded"
                ):
                    return False

                attempts_by_run: dict[str, list[AgentAttempt]] = {}
                for attempt in tree.attempts:
                    attempts_by_run.setdefault(attempt.agent_run_id, []).append(attempt)
                main_attempts = sorted(
                    attempts_by_run.get(main.id, []), key=lambda item: item.ordinal
                )
                if (
                    [(item.phase, item.ordinal) for item in main_attempts]
                    != [("planning", 1), ("synthesis", 2)]
                    or main.attempt_id != main_attempts[0].id
                    or not self._agent_and_attempts_match_binding(
                        main,
                        main_attempts,
                        bindings.get("main"),
                    )
                ):
                    return False

                for slot_key, assignment in assignments.items():
                    child = children_by_slot[slot_key]
                    child_attempts = attempts_by_run.get(child.id, [])
                    required_access = {
                        "workspace_write": "workspace_write",
                        "artifact": "artifact_only",
                        "review": "candidate_read",
                    }[assignment.delegate_purpose]
                    if (
                        child.delivery_id != run.delivery_id
                        or child.pipeline_revision_id != snapshot.pipeline_revision_id
                        or child.workcell_run_id != run.id
                        or child.binding_site != f"{run.stage_path}:{slot_key}"
                        or child.parent_agent_run_id != main.id
                        or child.root_agent_run_id != main.id
                        or child.depth != 1
                        or child.slot_key != slot_key
                        or child.delegate_purpose != assignment.delegate_purpose
                        or child.workspace_access != required_access
                        or child.status != "succeeded"
                        or len(child_attempts) != 1
                        or child_attempts[0].phase != "delegate"
                        or child_attempts[0].ordinal != 1
                        or child.attempt_id != child_attempts[0].id
                        or not self._agent_and_attempts_match_binding(
                            child,
                            child_attempts,
                            bindings.get(slot_key),
                        )
                    ):
                        return False
                    if not self._artifact_envelopes_are_valid(child):
                        return False
                if set(attempts_by_run) != {item.id for item in tree.agent_runs}:
                    return False
        except Exception:
            return False
        return True

    def _agent_and_attempts_match_binding(
        self,
        run: AgentRun,
        attempts: list[AgentAttempt],
        binding: FrozenSlotBinding | None,
    ) -> bool:
        if binding is None:
            return False
        frozen = binding.deployment_snapshot
        return not (
            run.deployment_snapshot != frozen
            or run.resolved_binding_hash != binding.resolved_provider_binding_hash
            or any(
                not _run_matches_frozen_binding(
                    run,
                    attempt,
                    frozen,
                    expected_adapter="codex.cli",
                )
                or attempt.error_code is not None
                or attempt.finished_at is None
                or attempt.result_artifact_sha256 is None
                or not self._artifact_sha_exists(attempt.result_artifact_sha256)
                for attempt in attempts
            )
        )

    def _artifact_sha_exists(self, digest: Sha256) -> bool:
        target = self.artifacts.root / "sha256" / str(digest)[:2] / str(digest)
        try:
            payload = target.read_bytes()
        except OSError:
            return False
        return sha256_bytes(payload) == digest

    def _artifact_envelopes_are_valid(self, run: AgentRun) -> bool:
        for envelope in run.artifact_envelopes:
            if envelope.reference is not None:
                self.artifacts.get_bytes(envelope.reference)
            elif envelope.content is None or envelope.sha256 != sha256_json(envelope.content):
                return False
        return True

    def _verify_workcell_results(
        self,
        final_trees: tuple[WorkcellRunTree, ...],
    ) -> bool:
        if not final_trees:
            return False
        try:
            for tree in final_trees:
                result = tree.result
                if result is None or not _result_hash_is_valid(result):
                    return False
                for reference in result.output_artifact_references:
                    self.artifacts.get_bytes(reference)
                if tree.verification is not None:
                    verification = tree.verification
                    if (
                        tree.result_validation is not None
                        or verification.status != "passed"
                        or not _verification_hash_is_valid(verification)
                        or result.candidate_sha != verification.candidate_sha
                        or result.diff_sha256 != verification.diff_sha256
                        or result.verification_sha256 != verification.sha256
                    ):
                        return False
                else:
                    validation = tree.result_validation
                    if (
                        validation is None
                        or validation.status != "passed"
                        or not _result_validation_hash_is_valid(validation)
                        or result.candidate_sha is not None
                        or result.diff_sha256 is not None
                        or result.verification_sha256 != validation.sha256
                        or result.output_artifact_references != validation.artifact_references
                    ):
                        return False
                    for reference in validation.artifact_references:
                        self.artifacts.get_bytes(reference)
                if set(result.review_artifact_ids) != {review.id for review in tree.reviews}:
                    return False
        except Exception:
            return False
        return True

    def _verify_knowledge(
        self,
        delivery: DeliveryRun,
        final_trees: tuple[WorkcellRunTree, ...],
    ) -> tuple[bool, Sha256 | None]:
        snapshot = delivery.delivery_execution_snapshot
        guard = self.knowledge_guard
        if snapshot is None or guard is None:
            return False, None
        required = tuple(
            sorted(
                stage_path
                for stage_path, payload in snapshot.knowledge_context_bindings.items()
                if isinstance(payload, dict) and payload.get("required") is True
            )
        )
        if required != tuple(sorted(KNOWLEDGE_CONTEXT_STAGE_PATHS)):
            return False, None
        try:
            preparation = self.knowledge.get_for_delivery(delivery.id)
            preparation_input = delivery.knowledge_preparation_input
            raw_stamp = snapshot.knowledge_authorization_stamp
            stamp = KnowledgeAuthorizationStampV1.model_validate(raw_stamp)
            if (
                preparation is None
                or preparation.status != "succeeded"
                or preparation.id != delivery.knowledge_preparation_run_id
                or preparation_input is None
                or preparation.preparation_input != preparation_input
                or preparation.input_sha256 != preparation_input.input_sha256
                or preparation_input.input_sha256
                != sha256_json(preparation_input.model_dump(mode="json", exclude={"input_sha256"}))
                or preparation.knowledge_binding_hash
                != sha256_json(preparation_input.stage_bindings)
                or preparation_input.stage_bindings != snapshot.knowledge_context_bindings
                or preparation_input.pipeline_revision_id != snapshot.pipeline_revision_id
                or preparation_input.pipeline_revision_sha256 != snapshot.pipeline_revision_sha256
                or preparation_input.project_id != delivery.project_id
                or preparation_input.delivery_id != delivery.id
                or preparation.authorization_stamp is None
                or preparation.authorization_stamp != stamp
                or not stamp.approvals
                or not stamp.connections
                or not _authorization_stamp_hash_is_valid(stamp)
                or preparation.authorization_epoch_hash != stamp.authorization_epoch_hash
                or preparation.final_snapshot is None
                or preparation.final_snapshot != snapshot
                or snapshot.knowledge_preparation_input_sha256 != preparation_input.input_sha256
            ):
                return False, None
            stage_results = {
                item.stage_path: item for item in self.knowledge.list_stage_results(preparation.id)
            }
            if set(stage_results) != set(required):
                return False, None
            contexts = []
            for stage_path in required:
                context = snapshot.knowledge_contexts.get(stage_path)
                stage_result = stage_results.get(stage_path)
                binding = snapshot.knowledge_context_bindings[stage_path]
                if context is None or stage_path in snapshot.knowledge_context_unavailable:
                    return False, None
                if (
                    stage_result is None
                    or stage_result.context != context
                    or stage_result.preparation_run_id != preparation.id
                    or stage_result.retrieval_policy_revision_id
                    != binding.get("retrieval_policy_revision_id")
                    or context.authorization_epoch_hash != stamp.authorization_epoch_hash
                ):
                    return False, None
                if guard.admit(delivery, stage_path) is None:
                    return False, None
                contexts.append(
                    {
                        "stage_path": stage_path,
                        "artifact_sha256": context.artifact_reference.sha256,
                        "citation_ids": context.citation_ids,
                        "authorization_epoch_hash": context.authorization_epoch_hash,
                    }
                )
            if "requirements" in required:
                if delivery.requirements is None:
                    return False, None
                guard.validate_citations(
                    delivery,
                    "requirements",
                    delivery.requirements.knowledge_citation_ids,
                )
            if "tasking" in required:
                if delivery.task is None:
                    return False, None
                guard.validate_citations(
                    delivery,
                    "tasking",
                    delivery.task.knowledge_citation_ids,
                )
            by_stage = {tree.workcell_run.stage_path: tree for tree in final_trees}
            for stage_path in required:
                tree = by_stage.get(stage_path)
                if tree is None:
                    continue
                if tree.result is None:
                    return False, None
                guard.validate_citations(
                    delivery,
                    stage_path,
                    tree.result.knowledge_citation_ids,
                )
        except Exception:
            return False, None
        return True, sha256_json(contexts)

    def _verify_candidates(
        self,
        delivery: DeliveryRun,
        final_trees: tuple[WorkcellRunTree, ...],
    ) -> tuple[bool, tuple[WorkspaceCandidateV2, ...]]:
        snapshot = delivery.delivery_execution_snapshot
        candidates = self.releases.list_candidates(delivery.id)
        if snapshot is None:
            return False, candidates
        contract = snapshot.release_contract_snapshot
        by_workcell = {item.workcell_key: item for item in candidates}
        if (
            len(by_workcell) != len(candidates)
            or set(by_workcell) != set(contract)
            or len(contract) != 4
        ):
            return False, candidates
        tree_by_workcell: dict[str, WorkcellRunTree] = {}
        for tree in final_trees:
            if tree.result is not None and tree.result.candidate_sha is not None:
                tree_by_workcell[tree.workcell_run.workcell_key] = tree
        try:
            for workcell_key in contract:
                candidate = by_workcell[workcell_key]
                candidate_tree = tree_by_workcell.get(workcell_key)
                projection = delivery.workcell_candidates.get(workcell_key)
                if (
                    candidate_tree is None
                    or projection is None
                    or candidate.adapter_type != "external-git"
                    or candidate.project_id != delivery.project_id
                    or not _candidate_hash_is_valid(candidate)
                ):
                    return False, candidates
                verification = candidate_tree.verification
                validation = candidate_tree.result_validation
                result = candidate_tree.result
                assert result is not None
                if (
                    verification is None
                    or verification.status != "passed"
                    or validation is not None
                    or not _verification_hash_is_valid(verification)
                    or not _result_hash_is_valid(result)
                    or result.candidate_sha != candidate.candidate_revision
                    or result.diff_sha256 != candidate.diff_sha256
                    or result.verification_sha256 != candidate.verification_sha256
                    or tuple(result.review_artifact_ids) != candidate.review_artifact_ids
                    or projection.candidate_id != candidate.id
                    or projection.candidate_revision != candidate.candidate_revision
                    or projection.diff_sha256 != candidate.diff_sha256
                    or projection.verification_sha256 != candidate.verification_sha256
                    or projection.review_artifact_ids != candidate.review_artifact_ids
                    or projection.evidence_sha256 != candidate.evidence_sha256
                ):
                    return False, candidates
                reviews = {item.id: item for item in candidate_tree.reviews}
                if set(reviews) != set(candidate.review_artifact_ids):
                    return False, candidates
                for review_id in candidate.review_artifact_ids:
                    review = reviews[review_id]
                    reviewer = next(
                        (
                            item
                            for item in candidate_tree.agent_runs
                            if item.id == review.reviewer_agent_run_id
                        ),
                        None,
                    )
                    if (
                        reviewer is None
                        or review.reviewer_binding_hash != reviewer.resolved_binding_hash
                        or not _review_matches_candidate(review, candidate)
                        or not _review_hash_is_valid(review)
                    ):
                        return False, candidates
                    self.artifacts.get_bytes(review.artifact_reference)
                pr = self.releases.get_pr(candidate.id)
                if pr is None or not _pr_matches_candidate(pr, candidate):
                    return False, candidates
        except Exception:
            return False, candidates
        return True, tuple(by_workcell[key] for key in contract)

    def _verify_bundle(
        self,
        delivery: DeliveryRun,
        candidates: tuple[WorkspaceCandidateV2, ...],
    ) -> tuple[bool, ReleaseBundleV2 | None]:
        snapshot = delivery.delivery_execution_snapshot
        try:
            bundle = self.releases.get_bundle(delivery.id)
        except (KeyError, ValueError):
            return False, None
        if snapshot is None:
            return False, bundle
        try:
            pr_hashes = [
                cast(GitHubPRReceipt, self.releases.get_pr(item.id)).receipt_sha256
                for item in bundle.candidates
            ]
        except (AttributeError, TypeError):
            return False, bundle
        payload = {
            "delivery_id": delivery.id,
            "project_id": delivery.project_id,
            "pipeline_revision_id": snapshot.pipeline_revision_id,
            "release_contract_snapshot": snapshot.release_contract_snapshot,
            "candidate_evidence_sha256": [item.evidence_sha256 for item in bundle.candidates],
            "pr_receipt_sha256": pr_hashes,
            "policy_version": "external-forward-only-v1",
        }
        return (
            bundle.status == "verified"
            and bundle.project_id == delivery.project_id
            and bundle.pipeline_revision_id == snapshot.pipeline_revision_id
            and bundle.release_contract_snapshot == snapshot.release_contract_snapshot
            and tuple(item.id for item in bundle.candidates)
            == tuple(item.id for item in candidates)
            and bundle.bundle_sha256 == sha256_json(payload)
            and delivery.release_bundle_v2_sha256 == bundle.bundle_sha256
        ), bundle

    def _verify_remote_apply(
        self,
        delivery: DeliveryRun,
        bundle: ReleaseBundleV2 | None,
    ) -> tuple[bool, tuple[RemoteApplyReceipt, ...]]:
        receipts = self.releases.list_remote_receipts(delivery.id)
        attempt = self.releases.get_attempt(delivery.id)
        if bundle is None or self.remote is None or attempt is None:
            return False, receipts
        if (
            attempt.status != "completed"
            or attempt.project_id != delivery.project_id
            or attempt.bundle_sha256 != bundle.bundle_sha256
            or len(receipts) != len(bundle.candidates)
        ):
            return False, receipts
        try:
            for ordinal, (candidate, receipt) in enumerate(
                zip(bundle.candidates, receipts, strict=True)
            ):
                if not _receipt_matches_candidate(receipt, candidate, ordinal):
                    return False, receipts
                if self.remote.revision(candidate) != candidate.candidate_revision:
                    return False, receipts
        except Exception:
            return False, receipts
        return True, receipts

    def _verify_manifest(
        self,
        delivery: DeliveryRun,
        bundle: ReleaseBundleV2 | None,
        receipts: tuple[RemoteApplyReceipt, ...],
    ) -> tuple[bool, ReleaseManifestV2 | None]:
        manifest = self.releases.get_manifest(delivery.project_id)
        if manifest is None or bundle is None:
            return False, manifest
        payload = {
            "project_id": bundle.project_id,
            "delivery_id": bundle.delivery_id,
            "pipeline_revision_id": bundle.pipeline_revision_id,
            "bundle_sha256": bundle.bundle_sha256,
            "repositories": [item.model_dump(mode="json") for item in receipts],
            "policy_version": "external-forward-only-v1",
        }
        return (
            manifest.status == "active"
            and manifest.delivery_id == delivery.id
            and manifest.pipeline_revision_id == bundle.pipeline_revision_id
            and manifest.bundle_sha256 == bundle.bundle_sha256
            and manifest.repositories == receipts
            and manifest.manifest_sha256 == sha256_json(payload)
            and delivery.release_manifest_v2_sha256 == manifest.manifest_sha256
        ), manifest

    def _verify_release_health(self, delivery: DeliveryRun, bundle: ReleaseBundleV2 | None) -> bool:
        if bundle is None:
            return False
        health = self.releases.get_health(delivery.project_id)
        return (
            health.status == "healthy"
            and health.delivery_id == delivery.id
            and health.bundle_sha256 == bundle.bundle_sha256
            and health.error_code is None
        )


def write_release_acceptance_report_v2(
    report_dir: Path,
    report: ReleaseAcceptanceReportV2,
) -> tuple[Path, Path]:
    """Persist one sanitized report without changing Delivery or Release state."""

    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%dT%H%M%SZ")
    subject = hashlib.sha256(f"{report.project_id}:{report.delivery_id}".encode()).hexdigest()[:12]
    stem = f"{timestamp}-release-acceptance-v2-{subject}-{report.report_sha256[:12]}"
    json_path = report_dir / f"{stem}.json"
    markdown_path = report_dir / f"{stem}.md"
    _atomic_write(
        json_path,
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
    )
    lines = [
        "# Agent-Team-OS Release Acceptance V2",
        "",
        f"- Capability: `{report.capability}`",
        f"- Kind: `{report.kind}`",
        f"- Project: `{report.project_id}`",
        f"- Delivery: `{report.delivery_id}`",
        f"- Status: `{report.status}`",
        f"- Counters: `FAIL={report.fail}` / `WARN={report.warn}` / `skipped={report.skipped}`",
        f"- Report SHA-256: `{report.report_sha256}`",
        "",
        "| Check | Status | Evidence SHA-256 | Detail |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(f"| {check.code} | {check.status} | `{check.evidence_sha256}` | {detail} |")
    _atomic_write(markdown_path, "\n".join(lines) + "\n")
    return json_path, markdown_path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _check(
    code: str,
    passed: bool,
    *,
    passed_detail: str,
    failed_detail: str,
    evidence: object,
) -> ReleaseAcceptanceCheckV2:
    return ReleaseAcceptanceCheckV2(
        code=code,
        status="passed" if passed else "failed",
        detail=passed_detail if passed else failed_detail,
        evidence_sha256=sha256_json(evidence),
    )


def _planning_runtime_contract(
    resolved_provider_bindings: dict[str, dict[str, object]],
) -> tuple[str, str, str] | None:
    """Resolve one explicit Planning contract from the frozen Pipeline snapshot."""

    contracts: set[tuple[str, str, str]] = set()
    for site in _PLANNING_BINDING_SITES:
        frozen = resolved_provider_bindings.get(site)
        if not isinstance(frozen, dict):
            return None
        deployment = frozen.get("deployment")
        runtime_identity = frozen.get("runtime_identity")
        if not isinstance(deployment, dict) or not isinstance(runtime_identity, str):
            return None
        if runtime_identity in {"deterministic-test", "codex-simulated-hermes"}:
            return None
        provider_id = deployment.get("provider_id")
        adapter_id = deployment.get("adapter_id")
        if provider_id == "hermes-provider" and adapter_id == "hermes.acp":
            contracts.add(("HERMES_PLANNING_ATTEMPTS_VERIFIED", "hermes.acp", "Hermes"))
        elif provider_id == "codex-cli-provider" and adapter_id == "codex.cli":
            contracts.add(("CODEX_PLANNING_ATTEMPTS_VERIFIED", "codex.cli", "Codex"))
        else:
            return None
    return next(iter(contracts)) if len(contracts) == 1 else None


def _run_matches_frozen_binding(
    run: AgentRun,
    attempt: AgentAttempt | None,
    frozen: object,
    *,
    expected_adapter: str,
) -> bool:
    if attempt is None or not isinstance(frozen, dict):
        return False
    deployment = frozen.get("deployment")
    binding = frozen.get("binding")
    runtime_identity = frozen.get("runtime_identity")
    if not isinstance(deployment, dict) or not isinstance(binding, dict):
        return False
    adapter = deployment.get("adapter_id")
    binding_hash = binding.get("binding_fingerprint")
    return (
        adapter == expected_adapter
        and isinstance(runtime_identity, str)
        and runtime_identity not in {"deterministic-test", "codex-simulated-hermes"}
        and run.status == "succeeded"
        and run.resolved_binding_hash == binding_hash
        and run.runtime_identity == runtime_identity
        and attempt.status == "succeeded"
        and attempt.provider_binding_hash == binding_hash
        and attempt.runtime_identity == runtime_identity
    )


def _workcell_state_evidence(
    delivery: DeliveryRun,
    trees: tuple[WorkcellRunTree, ...],
    final_trees: tuple[WorkcellRunTree, ...],
) -> dict[str, object]:
    snapshot = delivery.delivery_execution_snapshot
    return {
        "delivery_id": delivery.id,
        "required_stage_paths": (
            () if snapshot is None else tuple(sorted(snapshot.workcell_stage_map))
        ),
        "runs": [
            {
                "id": item.workcell_run.id,
                "stage_path": item.workcell_run.stage_path,
                "loop_iteration": item.workcell_run.loop_iteration,
                "status": item.workcell_run.status,
                "snapshot_sha256": item.workcell_run.workcell_snapshot_sha256,
            }
            for item in trees
        ],
        "final_run_ids": [item.workcell_run.id for item in final_trees],
    }


def _workcell_evidence_sha256(trees: tuple[WorkcellRunTree, ...]) -> Sha256:
    return sha256_json(
        [
            {
                "run_id": tree.workcell_run.id,
                "stage_path": tree.workcell_run.stage_path,
                "loop_iteration": tree.workcell_run.loop_iteration,
                "snapshot_sha256": tree.workcell_run.workcell_snapshot_sha256,
                "attempts": [
                    {
                        "id": item.id,
                        "binding": item.provider_binding_hash,
                        "status": item.status,
                        "result": item.result_artifact_sha256,
                    }
                    for item in tree.attempts
                ],
                "verification": (None if tree.verification is None else tree.verification.sha256),
                "reviews": [item.sha256 for item in tree.reviews],
                "result": None if tree.result is None else tree.result.sha256,
                "validation": (
                    None if tree.result_validation is None else tree.result_validation.sha256
                ),
            }
            for tree in trees
        ]
    )


def _candidate_hash_is_valid(candidate: WorkspaceCandidateV2) -> bool:
    payload = {
        "delivery_id": candidate.delivery_id,
        "project_id": candidate.project_id,
        "workcell_key": candidate.workcell_key,
        "workspace_binding_id": candidate.workspace_binding_id,
        "repository_uri": candidate.repository_uri,
        "adapter_type": candidate.adapter_type,
        "base_revision": candidate.base_revision,
        "candidate_revision": candidate.candidate_revision,
        "diff_sha256": candidate.diff_sha256,
        "verification_sha256": candidate.verification_sha256,
        "review_artifact_ids": candidate.review_artifact_ids,
        "candidate_branch": candidate.candidate_branch,
        "status": candidate.status,
    }
    return candidate.evidence_sha256 == sha256_json(payload)


def _review_matches_candidate(
    review: ReviewArtifact,
    candidate: WorkspaceCandidateV2,
) -> bool:
    return (
        review.candidate_sha == candidate.candidate_revision
        and review.diff_sha256 == candidate.diff_sha256
        and not review.blocking_findings
    )


def _verification_hash_is_valid(verification: CandidateVerification) -> bool:
    payload = {
        "workcell_run_id": verification.workcell_run_id,
        "writer_agent_run_id": verification.writer_agent_run_id,
        "candidate_sha": verification.candidate_sha,
        "diff_sha256": verification.diff_sha256,
        "status": verification.status,
        "report": verification.report,
    }
    return verification.sha256 == sha256_json(payload)


def _review_hash_is_valid(review: ReviewArtifact) -> bool:
    payload = {
        "workcell_run_id": review.workcell_run_id,
        "reviewer_binding_hash": review.reviewer_binding_hash,
        "reviewer_agent_run_id": review.reviewer_agent_run_id,
        "candidate_sha": review.candidate_sha,
        "diff_sha256": review.diff_sha256,
        "blocking_findings": [item.model_dump(mode="json") for item in review.blocking_findings],
        "artifact_reference": review.artifact_reference.model_dump(mode="json"),
    }
    return review.sha256 == sha256_json(payload)


def _result_hash_is_valid(result: WorkcellResult) -> bool:
    payload = {
        "workcell_run_id": result.workcell_run_id,
        "candidate_sha": result.candidate_sha,
        "diff_sha256": result.diff_sha256,
        "verification_sha256": result.verification_sha256,
        "review_artifact_ids": result.review_artifact_ids,
        "output_artifact_references": [
            item.model_dump(mode="json") for item in result.output_artifact_references
        ],
        "knowledge_citation_ids": result.knowledge_citation_ids,
    }
    return result.sha256 == sha256_json(payload)


def _result_validation_hash_is_valid(
    validation: WorkcellResultValidation,
) -> bool:
    payload = {
        "workcell_run_id": validation.workcell_run_id,
        "status": validation.status,
        "artifact_references": [
            item.model_dump(mode="json") for item in validation.artifact_references
        ],
        "report": validation.report,
    }
    return validation.sha256 == sha256_json(payload)


def _authorization_stamp_hash_is_valid(stamp: KnowledgeAuthorizationStampV1) -> bool:
    payload = stamp.model_dump(
        mode="json",
        exclude={"authorization_epoch_hash"},
    )
    return stamp.authorization_epoch_hash == sha256_json(payload)


def _pr_matches_candidate(
    receipt: GitHubPRReceipt,
    candidate: WorkspaceCandidateV2,
) -> bool:
    payload = {
        "candidate_id": candidate.id,
        "pull_request_id": receipt.pull_request_id,
        "url": receipt.url,
        "base_branch": receipt.base_branch,
        "head_branch": receipt.head_branch,
        "head_candidate_sha": receipt.head_candidate_sha,
        "state": receipt.state,
    }
    return (
        receipt.state == "open"
        and receipt.base_branch == "main"
        and receipt.head_branch == candidate.candidate_branch
        and receipt.head_candidate_sha == candidate.candidate_revision
        and receipt.receipt_sha256 == sha256_json(payload)
    )


def _receipt_matches_candidate(
    receipt: RemoteApplyReceipt,
    candidate: WorkspaceCandidateV2,
    ordinal: int,
) -> bool:
    payload = {
        "delivery_id": candidate.delivery_id,
        "ordinal": ordinal,
        "candidate_id": candidate.id,
        "workcell_key": candidate.workcell_key,
        "repository_uri": candidate.repository_uri,
        "before_revision": candidate.base_revision,
        "candidate_revision": candidate.candidate_revision,
        "after_revision": candidate.candidate_revision,
        "recovered": receipt.recovered,
    }
    return (
        receipt.delivery_id == candidate.delivery_id
        and receipt.ordinal == ordinal
        and receipt.candidate_id == candidate.id
        and receipt.workcell_key == candidate.workcell_key
        and receipt.repository_uri == candidate.repository_uri
        and receipt.before_revision == candidate.base_revision
        and receipt.candidate_revision == candidate.candidate_revision
        and receipt.after_revision == candidate.candidate_revision
        and receipt.receipt_sha256 == sha256_json(payload)
    )
