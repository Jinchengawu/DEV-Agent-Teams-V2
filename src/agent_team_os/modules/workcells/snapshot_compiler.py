from __future__ import annotations

from collections.abc import Callable

from ...delivery import (
    DeliveryExecutionSnapshot,
    DeliveryMethodSnapshot,
    DeliveryWorkspaceSnapshot,
)
from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json
from ..artifacts import ArtifactReference
from ..orchestration import PipelineCatalog, WorkcellStageBinding
from ..projects.ports import ProjectRepository
from .domain import WorkcellDefinition
from .execution_domain import (
    FrozenSlotBinding,
    WorkcellExecutionSnapshot,
    WorkcellWorkspaceSnapshot,
)
from .project_application import ProjectWorkcellGovernance


class DeliveryExecutionSnapshotCompiler:
    """Freeze Team, Pipeline, Provider, Workspace and Method revisions for one Delivery."""

    def __init__(
        self,
        *,
        governance: ProjectWorkcellGovernance,
        projects: ProjectRepository,
        pipelines: PipelineCatalog,
        method_snapshot: Callable[[], DeliveryMethodSnapshot],
    ) -> None:
        self.governance = governance
        self.projects = projects
        self.pipelines = pipelines
        self.method_snapshot = method_snapshot

    def compile(
        self,
        project_id: str,
        pipeline_revision_id: str,
    ) -> DeliveryExecutionSnapshot:
        topology = self.governance.topology(project_id)
        if topology.project_status != "active" or topology.team_binding.status != "active":
            raise _error(
                "PROJECT_TEAM_NOT_ACTIVE",
                "项目 Team 尚未激活",
                "完成四仓 Workspace Verify 与 Team Activation 后创建 Delivery。",
            )
        revision = self.pipelines.resolve_active_revision(pipeline_revision_id)
        if not revision.workcell_stage_map or not revision.release_contract_snapshot:
            raise _error(
                "PIPELINE_WORKCELL_CONTRACT_REQUIRED",
                "所选 Pipeline 不是 Workcell Pipeline",
                "选择已经冻结 Workcell Stage Map 与 Release Contract 的 Published Revision。",
            )
        team_keys = {item.workcell_key for item in topology.team_revision.workcells}
        mapped_keys = {
            item.workcell_key for item in revision.workcell_stage_map.values()
        }
        unknown = sorted(mapped_keys - team_keys)
        if unknown:
            raise _error(
                "PIPELINE_TEAM_WORKCELL_MISMATCH",
                "Pipeline 引用了 TeamTemplate 中不存在的 Workcell",
                "重新选择兼容的 Team 与 Pipeline Revision：" + "、".join(unknown),
            )
        release_keys = set(revision.release_contract_snapshot)
        if not release_keys.issubset(team_keys):
            raise _error(
                "RELEASE_CONTRACT_TEAM_WORKCELL_MISMATCH",
                "Release Contract 引用了 TeamTemplate 中不存在的 Workcell",
                "发布兼容的 Pipeline Revision。",
            )
        for stage_path, stage in revision.workcell_stage_map.items():
            for slot, site in stage.slot_bindings.items():
                snapshot = revision.resolved_provider_bindings.get(site)
                if snapshot is None:
                    raise _error(
                        "PIPELINE_WORKCELL_SLOT_BINDING_MISSING",
                        f"Stage {stage_path} 的 {slot} 缺少冻结 Provider Binding",
                        "重新校验并发布 Pipeline Revision。",
                    )
        workspace_by_id = {item.id: item for item in topology.workspace_bindings}
        workcell_binding_by_key = {
            item.workcell_key: item for item in topology.workcell_bindings
        }
        workspaces: list[DeliveryWorkspaceSnapshot] = []
        for definition in topology.team_revision.workcells:
            assignment = workcell_binding_by_key.get(definition.workcell_key)
            workspace = (
                None
                if assignment is None
                else workspace_by_id.get(assignment.workspace_binding_id)
            )
            if (
                workspace is None
                or workspace.status != "ready"
                or workspace.verification_sha256 is None
            ):
                raise _error(
                    "PROJECT_WORKSPACE_NOT_VERIFIED",
                    f"Workcell {definition.workcell_key} 没有已验证 Primary Workspace",
                    "重新验证该 Workspace 后创建 Delivery。",
                )
            base_revision = workspace.verification.get("main_sha")
            if not isinstance(base_revision, str):
                base_revision = workspace.verification.get("remote_main_sha")
            if not isinstance(base_revision, str):
                raise _error(
                    "WORKSPACE_BASE_REVISION_MISSING",
                    f"Workspace {workspace.id} 的验证回执缺少 main SHA",
                    "重新执行 Workspace Verify。",
                )
            workspaces.append(
                DeliveryWorkspaceSnapshot(
                    workcell_key=definition.workcell_key,
                    workspace_binding_id=workspace.id,
                    kind=workspace.kind,
                    adapter_type=workspace.adapter_type,
                    repository_uri=workspace.repository_uri,
                    base_revision=base_revision,
                    verification_sha256=workspace.verification_sha256,
                )
            )
        methods = self.method_snapshot()
        required_methods = {
            method_id
            for stage in revision.workcell_stage_map.values()
            for method_id in stage.delegate_methods.values()
        }
        missing_methods = sorted(required_methods - set(methods.method_entries))
        if missing_methods:
            raise _error(
                "PIPELINE_METHOD_ENTRY_NOT_QUALIFIED",
                "Pipeline 引用了未进入 Extension Snapshot 的 Method Entry",
                "安装并资格化缺失 Method Entry：" + "、".join(missing_methods),
            )
        project = self.projects.get(project_id)
        if project is None:
            raise _error("PROJECT_NOT_FOUND", "项目不存在", "刷新项目列表后重试。", 404)
        payload = {
            "project_id": project_id,
            "project_version": project.version,
            "team_template_revision_id": topology.team_binding.revision_id,
            "team_template_sha256": topology.team_binding.template_sha256,
            "team_workcells": {
                item.workcell_key: item.model_dump(mode="json")
                for item in topology.team_revision.workcells
            },
            "pipeline_revision_id": pipeline_revision_id,
            "pipeline_revision_sha256": revision.fingerprint,
            "workcell_stage_map": {
                key: value.model_dump(mode="json")
                for key, value in revision.workcell_stage_map.items()
            },
            "release_contract_snapshot": revision.release_contract_snapshot,
            "resolved_provider_bindings": revision.resolved_provider_bindings,
            "workspaces": [item.model_dump(mode="json") for item in workspaces],
            "method_snapshot": methods.model_dump(mode="json"),
        }
        return DeliveryExecutionSnapshot(
            project_id=project_id,
            project_version=project.version,
            team_template_revision_id=topology.team_binding.revision_id,
            team_template_sha256=topology.team_binding.template_sha256,
            team_workcells={
                item.workcell_key: item.model_dump(mode="json")
                for item in topology.team_revision.workcells
            },
            pipeline_revision_id=pipeline_revision_id,
            pipeline_revision_sha256=Sha256.validate(revision.fingerprint),
            workcell_stage_map={
                key: value.model_dump(mode="json")
                for key, value in revision.workcell_stage_map.items()
            },
            release_contract_snapshot=revision.release_contract_snapshot,
            resolved_provider_bindings=revision.resolved_provider_bindings,
            workspaces=tuple(workspaces),
            method_snapshot=methods,
            snapshot_sha256=sha256_json(payload),
        )


def compile_workcell_execution_snapshot(
    delivery: DeliveryExecutionSnapshot,
    stage_path: str,
    *,
    input_artifacts: tuple[ArtifactReference, ...] = (),
) -> WorkcellExecutionSnapshot:
    stage_payload = delivery.workcell_stage_map.get(stage_path)
    if stage_payload is None:
        raise _error(
            "PIPELINE_WORKCELL_STAGE_MAPPING_MISSING",
            f"Delivery Snapshot 没有 Stage {stage_path} 的 Workcell Binding",
            "修复 Published Pipeline Revision。",
        )
    try:
        stage = WorkcellStageBinding.model_validate(stage_payload)
    except ValueError as exc:
        raise _error(
            "DELIVERY_EXECUTION_SNAPSHOT_INVALID",
            "Workcell Stage Snapshot 结构无效",
            "将 Delivery 标记为失败并重新创建。",
        ) from exc
    workcell_key = stage.workcell_key
    workspace = next(
        (item for item in delivery.workspaces if item.workcell_key == workcell_key),
        None,
    )
    definition_payload = delivery.team_workcells.get(workcell_key)
    if workspace is None or not isinstance(definition_payload, dict):
        raise _error(
            "DELIVERY_WORKCELL_SNAPSHOT_INCOMPLETE",
            f"Workcell {workcell_key} 缺少 Team 或 Workspace Snapshot",
            "将 Delivery 标记为失败并重新创建。",
        )
    try:
        definition = WorkcellDefinition.model_validate(definition_payload)
    except ValueError as exc:
        raise _error(
            "DELIVERY_WORKCELL_SNAPSHOT_INCOMPLETE",
            f"Workcell {workcell_key} 的 Team Definition Snapshot 无效",
            "将 Delivery 标记为失败并重新创建。",
        ) from exc
    bindings: list[FrozenSlotBinding] = []
    for slot in ("main", "delegate_1", "delegate_2", "delegate_3"):
        site = stage.slot_bindings.get(slot)
        provider = (
            delivery.resolved_provider_bindings.get(site)
            if isinstance(site, str)
            else None
        )
        if not isinstance(provider, dict):
            raise _error(
                "DELIVERY_PROVIDER_SLOT_SNAPSHOT_INCOMPLETE",
                f"Workcell Stage {stage_path} 的 {slot} Provider Snapshot 缺失",
                "将 Delivery 标记为失败并重新创建。",
            )
        deployment = provider.get("deployment")
        binding = provider.get("binding")
        if not isinstance(deployment, dict) or not isinstance(binding, dict):
            raise _error(
                "DELIVERY_PROVIDER_SLOT_SNAPSHOT_INCOMPLETE",
                f"Workcell Stage {stage_path} 的 {slot} Deployment/Binding 缺失",
                "将 Delivery 标记为失败并重新创建。",
            )
        deployment_id = deployment.get("id")
        binding_hash = binding.get("binding_fingerprint")
        if not isinstance(deployment_id, str) or not isinstance(binding_hash, str):
            raise _error(
                "DELIVERY_PROVIDER_SLOT_SNAPSHOT_INCOMPLETE",
                f"Workcell Stage {stage_path} 的 {slot} Binding Identity 缺失",
                "将 Delivery 标记为失败并重新创建。",
            )
        bindings.append(
            FrozenSlotBinding(
                slot_key=slot,
                deployment_id=deployment_id,
                resolved_provider_binding_hash=Sha256.validate(binding_hash),
                deployment_snapshot=provider,
            )
        )
    return WorkcellExecutionSnapshot(
        team_template_revision_id=delivery.team_template_revision_id,
        team_template_sha256=delivery.team_template_sha256,
        pipeline_revision_id=delivery.pipeline_revision_id,
        pipeline_revision_sha256=delivery.pipeline_revision_sha256,
        stage_path=stage_path,
        workcell_key=workcell_key,
        workspace=WorkcellWorkspaceSnapshot(
            workspace_binding_id=workspace.workspace_binding_id,
            kind=workspace.kind,
            adapter_type=workspace.adapter_type,
            repository_uri=workspace.repository_uri,
            base_revision=workspace.base_revision,
            verification_sha256=workspace.verification_sha256,
        ),
        delegation_policy=definition.delegation_policy,
        slot_bindings=tuple(bindings),
        slot_method_bindings=stage.delegate_methods,
        slot_purpose_bindings=stage.delegate_purposes,
        method_snapshot_sha256=delivery.method_snapshot.qualification_sha256,
        input_artifacts=input_artifacts,
    )


def _error(
    code: str,
    title: str,
    repair: str,
    status_code: int = 409,
) -> ProductError:
    return ProductError(
        code=code,
        title=title,
        detail=title,
        repair=repair,
        status_code=status_code,
    )
