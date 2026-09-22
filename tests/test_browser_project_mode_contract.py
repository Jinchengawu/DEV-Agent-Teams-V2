from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"


def test_dag_apply_harness_explicitly_selects_standard_project() -> None:
    source = (SCRIPTS / "browser_pipeline_graph_e2e.py").read_text()
    selection = '_select_option(page, "启动方式", "标准项目")'
    assert selection in source
    assert source.index(selection) < source.index('name="创建并初始化独立工作区"')
    assert 'name="确认并启动"' in source
    assert 'name="接受候选并原子应用"' in source


def test_interaction_errors_reset_only_after_authentication() -> None:
    source = (SCRIPTS / "browser_interaction_closure_e2e.py").read_text()
    assert "_authenticate(page, args.url, password)\n        errors.clear()" in source
    assert "assert not errors" in source
