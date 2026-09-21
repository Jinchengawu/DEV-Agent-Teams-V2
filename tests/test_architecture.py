from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
DOMAIN_FORBIDDEN = {
    "acwm",
    "agentscope",
    "fastapi",
    "httpx",
    "sqlite3",
    "uvicorn",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def test_domain_modules_do_not_import_frameworks_or_infrastructure() -> None:
    domain_files = sorted((ROOT / "src" / "agent_team_os" / "modules").glob("**/*domain.py"))
    assert domain_files, "at least one golden domain module must exist"
    for path in domain_files:
        forbidden = _imports(path) & DOMAIN_FORBIDDEN
        assert not forbidden, f"{path.relative_to(ROOT)} imports forbidden modules: {forbidden}"


def test_web_features_do_not_import_other_feature_implementations() -> None:
    features = ROOT / "console" / "src" / "features"
    if not features.exists():
        return
    for path in features.glob("**/*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        own_feature = path.relative_to(features).parts[0]
        for other in (item.name for item in features.iterdir() if item.is_dir()):
            if other != own_feature:
                assert f"features/{other}" not in source, (
                    f"{path.relative_to(ROOT)} imports feature implementation {other}"
                )


def test_gate_composition_propagates_explicit_codex_auth_reference() -> None:
    """Gate and Preview must compose the same path-only Method auth boundary."""
    path = ROOT / "src" / "agent_team_os" / "gate_app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    method_arguments = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkcellStageDriver"
        for keyword in node.keywords
        if keyword.arg == "methods"
    ]

    assert len(method_arguments) == 1
    method_factory = method_arguments[0]
    assert isinstance(method_factory, ast.Call)
    assert isinstance(method_factory.func, ast.Attribute)
    assert method_factory.func.attr == "from_environment"
    assert isinstance(method_factory.func.value, ast.Name)
    assert method_factory.func.value.id == "ContentAddressedMethodRuntime"


def test_preview_and_gate_bootstrap_current_builtin_team_revision() -> None:
    for relative in ("src/agent_team_os/preview.py", "src/agent_team_os/gate_app.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ensure_builtin_software_delivery_team(team_templates)" in source
