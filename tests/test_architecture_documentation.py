from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture" / "ARCHITECTURE.md"
DIAGRAM_SOURCE = (
    ROOT
    / "docs"
    / "architecture"
    / "diagrams"
    / "agent-team-os-current.archify.json"
)
DIAGRAM_HTML = ROOT / "docs" / "assets" / "architecture" / "agent-team-os-current.html"
DIAGRAM_PNG = ROOT / "docs" / "assets" / "architecture" / "agent-team-os-current.png"
CURRENT_DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "readme-cn.md",
    ROOT / "docs" / "product" / "AGENT-TEAM-OS-PRODUCT.md",
    ARCHITECTURE,
    ROOT / "AGENTS.md",
    ROOT / "CONTEXT.md",
)


def test_architecture_entrypoint_has_truth_and_change_boundaries() -> None:
    document = ARCHITECTURE.read_text(encoding="utf-8")

    assert "truth_scope: repository_revision_containing_this_file" in document
    assert "initial_audit_baseline: 7401fa281a201728fa3cc504daa05d3a724fa7c6" in document
    for heading in (
        "## 1. 阅读规则与事实边界",
        "## 4. 唯一权威与边界",
        "## 5. 应用结构：FastAPI 模块化单体",
        "## 8. Pipeline 与 Workcell 执行",
        "## 9. Release V1/V2 与故障恢复",
        "## 12. Accepted Architecture Changes",
        "## 13. 架构变更台账",
        "## 14. Plan Architecture Review 与文档对账",
    ):
        assert heading in document

    for state in (
        "Implemented",
        "Deterministic Verified",
        "Live Blocked/Not Run",
        "Accepted/Not Implemented",
        "Superseded",
    ):
        assert state in document

    assert "代码存在、" in document
    assert "正式 Release 验收完成" in document
    assert "三个 v0.5.1 Feature Flag 默认均为关闭" in document
    assert "Gate A/B/C 本地闭环" in document
    assert "ACWM `0.5.1` Contract 已发布回锁" in document
    assert "execution_status=not_run" in document
    assert "ready` 不是 Live Gate 通过" in document
    assert "Live Readiness 和 Release Acceptance 按 Published Pipeline" in document
    assert "`codex-simulated-hermes` 仍保持可读" in document
    assert "CODEX_PLANNING_ATTEMPTS_VERIFIED" in document
    assert "Planning Role Turn（Codex / Hermes）" in document
    assert "`http.sync` 尚未接线" in document
    assert "逐 Attempt" in document
    assert "冻结 Citation 集" in document
    assert "hermes acp --check" in document
    assert "knowledge-sync-runtime-v1" in document
    assert "并发 2、最多 5 次尝试" in document
    assert "Source Head 级" in document
    assert "不共享 Git Workspace" in document
    assert "ARCH-20260902-02" in document
    assert "ARCH-20260902-04" in document
    assert "Release Acceptance V2" in document
    assert "Maturity: Deterministic Verified; Live Blocked/Not Run" in document
    assert "`knowledge-live-gate`" in document
    assert "Workcell Result" in document
    assert "Workcell Snapshot/DelegationPlan/" in document
    assert "Main-Child-Attempt 拓扑逐项绑定 Delivery Snapshot" in document
    assert "AgentScope 只承载单次 Attempt" in document
    assert "Runtime Adapter 不得隐藏派生" in document


def test_agents_prompt_requires_architecture_review_and_reconciliation() -> None:
    prompt = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "docs/architecture/ARCHITECTURE.md" in prompt
    review_flow = (
        "Draft Plan → Architecture Review → Revise Plan → Final Plan "
        "→ Implementation → Architecture Reconciliation"
    )
    assert review_flow in prompt
    for field in (
        "Architecture Impact",
        "Findings",
        "Required Revisions",
        "ADR Required",
        "Architecture Document Delta",
        "Outcome",
    ):
        assert field in prompt
    assert "None`、`Local`、`Cross-boundary` 或 `Critical" in prompt
    assert "Approved`、`Revise` 或 `Blocked" in prompt


def test_agentscope_attempt_runtime_authority_is_consistent() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    context = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    product = (ROOT / "docs" / "product" / "AGENT-TEAM-OS-PRODUCT.md").read_text(
        encoding="utf-8"
    )
    adr = (
        ROOT / "docs" / "architecture" / "ADR-0014-AGENT-WORKCELL-AUTHORITY.md"
    ).read_text(encoding="utf-8")

    for document in (agents, context, product, adr):
        assert "Workcell Composition" in document
        assert "Hidden Child" in document or "隐藏派生" in document

    superseded_authority = (
        "AgentScope owns Stage-local messages, sessions, memory, and role composition"
    )
    assert superseded_authority not in context
    assert "AgentScope 拥有 Stage 内通信和 Agent 组合" not in agents
    assert "AgentScope 仍然拥有 Stage 内组合" not in adr


def test_architecture_diagram_has_one_versioned_source_and_current_outputs() -> None:
    sources = sorted((DIAGRAM_SOURCE.parent).glob("*.archify.json"))
    assert sources == [DIAGRAM_SOURCE]

    diagram = json.loads(DIAGRAM_SOURCE.read_text(encoding="utf-8"))
    assert diagram["diagram_type"] == "architecture"
    assert diagram["meta"]["locale"] == "zh-CN"
    assert diagram["meta"]["quality_profile"] == "showcase"
    assert len(diagram["components"]) <= 14
    component_ids = [item["id"] for item in diagram["components"]]
    assert len(component_ids) == len(set(component_ids))
    assert {"knowledge", "gate_c_contract"}.issubset(component_ids)
    assert DIAGRAM_HTML.is_file()
    assert DIAGRAM_PNG.is_file()

    assets = DIAGRAM_HTML.parent
    assert not (assets / "agent-team-os-current.dark.png").exists()
    assert not (assets / "agent-team-os-current.zh-CN.dark.png").exists()
    assert not list(assets.glob("agent-team-os-current.visual-check*"))


def test_current_architecture_documentation_local_links_resolve() -> None:
    for document_path in CURRENT_DOCUMENTS:
        document = document_path.read_text(encoding="utf-8")
        targets = re.findall(r"\]\(([^)]+)\)", document)
        local_targets = [
            target
            for target in targets
            if not target.startswith(("http", "#", "mailto:"))
        ]
        for target in local_targets:
            path = target.split("#", 1)[0]
            assert (document_path.parent / path).resolve().exists(), (
                document_path,
                target,
            )


def test_current_documentation_does_not_reference_retired_architecture_images() -> None:
    retired = (
        "agent-team-os-current.dark.png",
        "agent-team-os-current.zh-CN.dark.png",
    )
    for path in CURRENT_DOCUMENTS:
        content = path.read_text(encoding="utf-8")
        assert all(item not in content for item in retired), path
        assert "17eea23" not in content, path
        assert "210 passed" not in content, path
        assert "69 tests" not in content, path
        assert "当前工作树尚未提交" not in content, path

    product = (ROOT / "docs" / "product" / "AGENT-TEAM-OS-PRODUCT.md").read_text(
        encoding="utf-8"
    )
    assert "尚未合入 `main`" not in product
    assert "origin/main@cfe597c05b3b0c65af57bf12d14b7f802fe7899f" in product
    assert "135 条 Path、164 个 HTTP Operation" in product
    assert "Global Role ∩ ProjectRole ∩ Approved Source Scope" in product
    assert "Release Acceptance V2" in product
    assert "107 条 Path" not in product
