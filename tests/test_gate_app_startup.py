"""隔离 Data Root 下的 Gate App 构造边界。"""

from importlib import import_module
from pathlib import Path

from pytest import MonkeyPatch

from agent_team_os import preview


def test_gate_app_construction_does_not_create_uninstalled_method_store(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("AGENT_TEAM_OS_DATA_DIR", str(data_root))
    monkeypatch.delenv("AGENT_TEAM_OS_GATE_CODEX_RUNTIME", raising=False)
    # This constructor smoke needs no built Console; keep all data writes in tmp_path.
    monkeypatch.setattr(
        preview, "resolve_product_root", lambda _root=None: Path(__file__).parents[1]
    )
    gate_app = import_module("agent_team_os.gate_app")
    app = gate_app.build_gate_app()
    assert app is not None
    assert data_root.exists()
    assert not (data_root / "method-packs").exists()
