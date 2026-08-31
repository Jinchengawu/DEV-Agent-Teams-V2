from __future__ import annotations

from ..orchestration import WorkcellStageBinding
from .application import TeamTemplateCatalog
from .domain import (
    DelegationPolicy,
    TeamTemplateCreate,
    TeamTemplateRevision,
    TeamTopology,
    TopologyLink,
    TopologyNode,
    WorkcellDefinition,
    WorkspaceRequirement,
)


def ensure_builtin_software_delivery_team(
    catalog: TeamTemplateCatalog,
    *,
    actor_id: str = "system",
) -> TeamTemplateRevision:
    """Seed the immutable four-repository organization used by the v0.5 pipeline."""

    try:
        return catalog.get_revision("software-delivery-team", 1)
    except KeyError:
        pass
    definition = TeamTemplateCreate(
        id="software-delivery-team",
        name="四仓软件交付团队",
        description=(
            "Design、Frontend、Backend、QA 各自拥有独立 Primary Git Repository。"
            "组织拓扑只表达责任与 Artifact 传递，不定义 Stage 顺序。"
        ),
        workcells=(
            _workcell("design", "Design", "生成交互与视觉契约并完成设计边界审查。"),
            _workcell(
                "frontend",
                "Frontend",
                "实现前端 Candidate，完成机器验证、代码审查与 UX Edge Review。",
            ),
            _workcell(
                "backend",
                "Backend",
                "实现后端 Candidate，完成机器验证、代码审查与 Security Edge Review。",
            ),
            _workcell(
                "qa",
                "QA",
                "先生成 Test Design/ATDD Artifact，再交付独立 QA Candidate 与 Trace Evidence。",
                purposes=("workspace_write", "artifact", "review"),
            ),
        ),
        topology=TeamTopology(
            nodes=(
                TopologyNode(workcell_key="design", x=40, y=160),
                TopologyNode(workcell_key="frontend", x=360, y=60),
                TopologyNode(workcell_key="backend", x=360, y=260),
                TopologyNode(workcell_key="qa", x=700, y=160),
            ),
            links=(
                TopologyLink(source_workcell_key="design", target_workcell_key="frontend"),
                TopologyLink(source_workcell_key="design", target_workcell_key="backend"),
                TopologyLink(source_workcell_key="frontend", target_workcell_key="qa"),
                TopologyLink(source_workcell_key="backend", target_workcell_key="qa"),
            ),
        ),
    )
    created = catalog.create(definition, actor_id=actor_id)
    validated = catalog.validate(created.draft.id, expected_version=created.draft.version)
    return catalog.publish(
        created.draft.id,
        expected_version=validated.version,
        actor_id=actor_id,
    )


def _workcell(
    key: str,
    name: str,
    responsibility: str,
    *,
    purposes: tuple[str, ...] = ("workspace_write", "review"),
) -> WorkcellDefinition:
    return WorkcellDefinition.model_validate(
        {
            "workcell_key": key,
            "name": name,
            "responsibility": responsibility,
            "primary_workspace": WorkspaceRequirement(kind="git_repository_v1"),
            "delegate_purposes": purposes,
            "delegation_policy": DelegationPolicy(
                max_children=3,
                max_concurrency=2,
                max_writers=1,
                max_depth=1,
                wall_clock_budget_seconds=900,
            ),
        }
    )


def builtin_workcell_stage_map() -> dict[str, WorkcellStageBinding]:
    return {
        "design-repair/design": _stage(
            "design",
            {
                "delegate_1": ("bmad-ux", "workspace_write"),
                "delegate_2": ("bmad-review", "review"),
                "delegate_3": ("bmad-review", "review"),
            },
        ),
        "qa-preparation-repair/qa-preparation": _stage(
            "qa",
            {
                "delegate_1": ("bmad-testarch-test-design", "artifact"),
                "delegate_2": ("bmad-testarch-atdd", "artifact"),
                "delegate_3": ("bmad-testarch-trace", "artifact"),
            },
        ),
        "frontend-repair/frontend": _stage(
            "frontend",
            {
                "delegate_1": ("bmad-build", "workspace_write"),
                "delegate_2": ("bmad-code-review", "review"),
                "delegate_3": ("bmad-review", "review"),
            },
        ),
        "backend-repair/backend": _stage(
            "backend",
            {
                "delegate_1": ("bmad-build", "workspace_write"),
                "delegate_2": ("bmad-code-review", "review"),
                "delegate_3": ("bmad-review", "review"),
            },
        ),
        "qa-delivery-repair/qa-delivery": _stage(
            "qa",
            {
                "delegate_1": ("bmad-testarch-automate", "workspace_write"),
                "delegate_2": ("bmad-testarch-test-review", "review"),
                "delegate_3": ("bmad-testarch-trace", "review"),
            },
        ),
    }


def builtin_release_contract() -> tuple[str, ...]:
    return ("design", "frontend", "backend", "qa")


def _stage(
    workcell_key: str,
    delegates: dict[str, tuple[str, str]],
) -> WorkcellStageBinding:
    stage_path = {
        "design": "design-repair/design",
        "frontend": "frontend-repair/frontend",
        "backend": "backend-repair/backend",
        "qa": (
            "qa-preparation-repair/qa-preparation"
            if delegates["delegate_1"][1] == "artifact"
            else "qa-delivery-repair/qa-delivery"
        ),
    }[workcell_key]
    return WorkcellStageBinding.model_validate(
        {
            "workcell_key": workcell_key,
            "slot_bindings": {
                slot: f"{stage_path}.{slot}"
                for slot in ("main", "delegate_1", "delegate_2", "delegate_3")
            },
            "delegate_methods": {
                slot: method for slot, (method, _purpose) in delegates.items()
            },
            "delegate_purposes": {
                slot: purpose for slot, (_method, purpose) in delegates.items()
            },
        }
    )
