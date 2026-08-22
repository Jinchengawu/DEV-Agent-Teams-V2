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
