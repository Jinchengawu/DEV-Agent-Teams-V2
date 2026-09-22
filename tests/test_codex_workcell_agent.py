import asyncio
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agent_team_os.infrastructure.acwm import CodexWorkcellAgent
from agent_team_os.modules.workcells import WorkcellAgentInvocation
from agent_team_os.shared.errors import ProductError


def test_codex_workcell_agent_returns_only_observable_structured_attempt(
    tmp_path: Path,
) -> None:
    event = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps({"blocking_findings": []}),
        },
    }
    code = (
        "import json,sys; sys.stdin.read(); "
        f"print(json.dumps({event!r}, ensure_ascii=False))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-1",
                workcell_run_id="workcell-1",
                agent_run_id="agent-1",
                phase="delegate",
                workcell_key="frontend",
                stage_path="frontend-repair/frontend",
                instruction="review",
                workspace=tmp_path,
                workspace_access="candidate_read",
                method_id="bmad-code-review",
            )
        )
    )

    assert output.runtime_identity == "codex-test"
    assert output.content == {"blocking_findings": []}


def test_codex_workcell_agent_allows_product_assigned_non_git_control_workspace(
    tmp_path: Path,
) -> None:
    event_code = """
import json
import sys

sys.stdin.read()
event = {
    "type": "item.completed",
    "item": {
        "type": "agent_message",
        "text": json.dumps({"argv": sys.argv[1:], "assignment_slots": []}),
    },
}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", event_code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-control-workspace",
                workcell_run_id="workcell-control-workspace",
                agent_run_id="agent-control-workspace",
                phase="planning",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="confirm assignments",
                workspace=tmp_path,
                workspace_access="none",
            )
        )
    )

    assert "--skip-git-repo-check" in output.content["argv"]
    assert output.content["assignment_slots"] == []


def test_codex_workcell_agent_bounds_shared_provider_concurrency(
    tmp_path: Path,
) -> None:
    state = tmp_path / "concurrency.json"
    code = """
import fcntl
import json
import os
import sys
import time

state = os.environ["AGENT_TEAM_OS_CONCURRENCY_STATE"]

def update(delta):
    with open(state, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        payload = json.loads(raw) if raw else {"active": 0, "maximum": 0}
        payload["active"] += delta
        payload["maximum"] = max(payload["maximum"], payload["active"])
        handle.seek(0)
        handle.truncate()
        json.dump(payload, handle)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

sys.stdin.read()
update(1)
time.sleep(0.15)
update(-1)
event = {
    "type": "item.completed",
    "item": {"type": "agent_message", "text": json.dumps({"ok": True})},
}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
        max_concurrency=2,
    )

    async def run_all() -> None:
        await asyncio.gather(
            *(
                agent.run(
                    WorkcellAgentInvocation(
                        delivery_id="delivery-concurrency",
                        workcell_run_id="workcell-concurrency",
                        agent_run_id=f"agent-concurrency-{index}",
                        phase="delegate",
                        workcell_key="frontend",
                        stage_path="frontend-repair/frontend",
                        instruction="review",
                        workspace=tmp_path,
                        workspace_access="candidate_read",
                        environment={
                            "AGENT_TEAM_OS_CONCURRENCY_STATE": str(state),
                        },
                    )
                )
                for index in range(3)
            )
        )

    asyncio.run(run_all())

    assert json.loads(state.read_text(encoding="utf-8")) == {
        "active": 0,
        "maximum": 2,
    }


@pytest.mark.parametrize(
    ("stderr", "expected_code"),
    [
        ("Error: authentication required", "CODEX_WORKCELL_AUTH_REQUIRED"),
        ("http/request failed: error sending request", "CODEX_WORKCELL_TRANSPORT_UNAVAILABLE"),
        ("request timed out", "CODEX_WORKCELL_TRANSPORT_TIMED_OUT"),
        ("model gpt-x unavailable", "CODEX_WORKCELL_MODEL_UNAVAILABLE"),
        ("unexpected provider failure", "CODEX_WORKCELL_ATTEMPT_FAILED"),
    ],
)
def test_codex_workcell_agent_persists_stable_nonzero_exit_classification(
    tmp_path: Path,
    stderr: str,
    expected_code: str,
) -> None:
    code = f"import sys; sys.stdin.read(); sys.stderr.write({stderr!r}); raise SystemExit(1)"
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    with pytest.raises(ProductError) as captured:
        asyncio.run(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-failure-classification",
                    workcell_run_id="workcell-failure-classification",
                    agent_run_id="agent-failure-classification",
                    phase="planning",
                    workcell_key="design",
                    stage_path="design-repair/design",
                    instruction="classify",
                    workspace=tmp_path,
                    workspace_access="none",
                )
            )
        )

    assert captured.value.code == expected_code
    assert captured.value.context == {
        "contract_version": "codex-cli-exit-diagnostic-v2",
        "exit_code": 1,
        "signals": [],
        "stderr_bytes": len(stderr.encode()),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "stable_category": expected_code,
    }
    assert stderr not in captured.value.detail


@pytest.mark.parametrize(
    ("stderr", "expected_code", "expected_signal"),
    [
        (
            "unexpected status 429; usage limit reached",
            "CODEX_WORKCELL_CAPACITY_EXHAUSTED",
            "http-429",
        ),
        (
            "unexpected status 403; access denied",
            "CODEX_WORKCELL_ACCESS_DENIED",
            "http-403",
        ),
        (
            "failed to load config: invalid configuration",
            "CODEX_WORKCELL_CONFIG_INVALID",
            "config-load-failed",
        ),
        (
            "stream disconnected before completion",
            "CODEX_WORKCELL_TRANSPORT_UNAVAILABLE",
            "stream-disconnected",
        ),
        (
            'event={"type":"turn.failed"}',
            "CODEX_WORKCELL_PROTOCOL_FAILED",
            "turn-failed",
        ),
    ],
)
def test_codex_workcell_agent_maps_safe_exit_signals(
    tmp_path: Path,
    stderr: str,
    expected_code: str,
    expected_signal: str,
) -> None:
    code = f"import sys; sys.stdin.read(); sys.stderr.write({stderr!r}); raise SystemExit(1)"
    agent = CodexWorkcellAgent(command=(sys.executable, "-c", code))

    with pytest.raises(ProductError) as captured:
        asyncio.run(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-signals",
                    workcell_run_id="workcell-signals",
                    agent_run_id="agent-signals",
                    phase="planning",
                    workcell_key="design",
                    stage_path="design-repair/design",
                    instruction="classify",
                    workspace=tmp_path,
                    workspace_access="none",
                )
            )
        )

    assert captured.value.code == expected_code
    assert expected_signal in captured.value.context["signals"]


def test_codex_workcell_agent_parses_only_the_last_agent_message(
    tmp_path: Path,
) -> None:
    progress = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "正在修改隔离工作区"},
    }
    final = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps(
                {"changed_files": ["design/health-contract-v1.json"], "knowledge_citation_ids": []}
            ),
        },
    }
    code = (
        "import json,sys; sys.stdin.read(); "
        f"print(json.dumps({progress!r}, ensure_ascii=False)); "
        f"print(json.dumps({final!r}, ensure_ascii=False))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-multiple-messages",
                workcell_run_id="workcell-multiple-messages",
                agent_run_id="agent-multiple-messages",
                phase="delegate",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="write",
                workspace=tmp_path,
                workspace_access="workspace_write",
                method_id="bmad-ux",
            )
        )
    )

    assert output.content == {"changed_files": ["design/health-contract-v1.json"]}


def test_codex_workcell_agent_freezes_method_project_root_in_instruction(
    tmp_path: Path,
) -> None:
    code = """
import json
import sys

instruction = sys.stdin.read()
event = {
    "type": "item.completed",
    "item": {
        "type": "agent_message",
        "text": json.dumps({"instruction": instruction}),
    },
}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-project-root",
                workcell_run_id="workcell-project-root",
                agent_run_id="agent-project-root",
                phase="delegate",
                workcell_key="frontend",
                stage_path="frontend-repair/frontend",
                instruction="write",
                workspace=tmp_path,
                workspace_access="workspace_write",
                method_id="bmad-build",
            )
        )
    )

    instruction = str(output.content["instruction"])
    assert f"Method Project Root\uff1a{tmp_path.resolve()}" in instruction
    assert (
        "{project-root} \u5fc5\u987b\u9010\u5b57\u66ff\u6362\u4e3a Method Project Root"
        in instruction
    )


def test_codex_workcell_agent_mounts_bmad_support_without_git_pollution(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    source = tmp_path / "verified-bmad-source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "render_skill.py").write_text("# verified renderer\n", encoding="utf-8")
    (scripts / "config_utils.py").write_text("# verified config\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    code = """
import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdin.read()
root = Path.cwd()
overlay = root / "_bmad"
codex_home_overlay = Path(os.environ["CODEX_HOME"]) / "_bmad"
renderer = (overlay / "scripts" / "render_skill.py").read_text(encoding="utf-8")
status = subprocess.check_output(["git", "status", "--short"], text=True)
(root / "src").mkdir(exist_ok=True)
(root / "src" / "health.py").write_text("STATUS = 'ok'\\n", encoding="utf-8")
payload = {
    "overlay_present": overlay.is_dir(),
    "renderer_verified": renderer == "# verified renderer\\n",
    "overlay_hidden_from_git": "_bmad" not in status,
    "runtime_source_leaked": "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE" in os.environ,
    "config_present": (overlay / "config.toml").is_file(),
    "bmm_config_present": (overlay / "bmm" / "config.yaml").is_file(),
    "codex_home_project_root_alias": (
        codex_home_overlay.resolve() == overlay.resolve()
        and (codex_home_overlay / "scripts" / "render_skill.py").is_file()
    ),
}
event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-overlay",
                workcell_run_id="workcell-overlay",
                agent_run_id="agent-overlay",
                phase="delegate",
                workcell_key="frontend",
                stage_path="frontend-repair/frontend",
                instruction="write",
                workspace=workspace,
                workspace_access="workspace_write",
                method_id="bmad-build",
                environment={
                    "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE": str(source),
                    "CODEX_HOME": str(codex_home),
                },
            )
        )
    )

    assert output.content == {
        "overlay_present": True,
        "renderer_verified": True,
        "overlay_hidden_from_git": True,
        "runtime_source_leaked": False,
        "config_present": True,
        "bmm_config_present": True,
        "codex_home_project_root_alias": True,
    }
    assert not (workspace / "_bmad").exists()
    assert not (codex_home / "_bmad").exists()
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=workspace, text=True
    ).splitlines() == ["?? src/"]


def test_codex_workcell_agent_mounts_bmad_support_for_read_only_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "review"
    workspace.mkdir()
    candidate = workspace / "candidate.txt"
    candidate.write_text("immutable candidate\n", encoding="utf-8")
    source = tmp_path / "verified-bmad-source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "render_skill.py").write_text("# verified renderer\n", encoding="utf-8")
    (scripts / "config_utils.py").write_text("# verified config\n", encoding="utf-8")
    candidate.chmod(0o444)
    workspace.chmod(0o555)
    code = """
import json
import sys
from pathlib import Path

sys.stdin.read()
root = Path.cwd()
overlay = root / "_bmad"
payload = {
    "overlay_present": overlay.is_dir(),
    "renderer_present": (overlay / "scripts" / "render_skill.py").is_file(),
    "candidate_writable": bool((root / "candidate.txt").stat().st_mode & 0o200),
    "overlay_writable": bool(overlay.stat().st_mode & 0o200),
}
event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}
print(json.dumps(event))
"""
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    try:
        output = asyncio.run(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-read-only-review",
                    workcell_run_id="workcell-read-only-review",
                    agent_run_id="agent-read-only-review",
                    phase="delegate",
                    workcell_key="design",
                    stage_path="design-repair/design",
                    instruction="review",
                    workspace=workspace,
                    workspace_access="candidate_read",
                    method_id="bmad-review",
                    environment={"AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE": str(source)},
                )
            )
        )

        assert output.content == {
            "overlay_present": True,
            "renderer_present": True,
            "candidate_writable": False,
            "overlay_writable": False,
        }
        assert not (workspace / "_bmad").exists()
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o555
        assert stat.S_IMODE(candidate.stat().st_mode) == 0o444
    finally:
        workspace.chmod(0o755)
        candidate.chmod(0o644)


def test_codex_workcell_agent_requires_a_citation_for_non_empty_context(
    tmp_path: Path,
) -> None:
    required_clause = (
        "允许列表非空时，knowledge_citation_ids 必须至少包含其中一个 ID"
    )
    authority_clause = (
        "只有该允许列表是 citation ID 的声明权威"
    )
    code = (
        "import json,sys; text=sys.stdin.read(); "
        f"required={required_clause!r} in text; "
        f"authority={authority_clause!r} in text; "
        "payload={'requires_citation': required, 'allowlist_is_authority': authority, "
        "'knowledge_citation_ids': ['citation-allowed']}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-citation",
                workcell_run_id="workcell-citation",
                agent_run_id="agent-citation",
                phase="planning",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="plan",
                workspace=tmp_path,
                workspace_access="none",
                allowed_knowledge_citation_ids=("citation-allowed",),
            )
        )
    )

    assert output.content == {
        "requires_citation": True,
        "allowlist_is_authority": True,
    }
    assert output.knowledge_citation_ids == ("citation-allowed",)


def test_cancelling_the_parent_task_terminates_the_codex_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = CodexWorkcellAgent(
            command=(
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.read(); time.sleep(30)",
            ),
            runtime_identity="codex-test",
        )
        task = asyncio.create_task(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-cancel",
                    workcell_run_id="workcell-cancel",
                    agent_run_id="agent-cancel",
                    phase="delegate",
                    workcell_key="frontend",
                    stage_path="frontend-repair/frontend",
                    instruction="write",
                    workspace=tmp_path,
                    workspace_access="workspace_write",
                    method_id="bmad-build",
                )
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await agent.cancel("agent-cancel") is False

    asyncio.run(scenario())


def test_codex_workcell_does_not_inherit_namespaced_service_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = "ghp_livecredentialmustnotleak"
    monkeypatch.setenv("AGENT_TEAM_OS_GITHUB_TOKEN", credential)
    agent = CodexWorkcellAgent(
        command=(
            sys.executable,
            "-c",
            "import os,sys; "
            "sys.stderr.write(os.environ.get('AGENT_TEAM_OS_GITHUB_TOKEN', 'not-inherited')); "
            "sys.exit(7)",
        ),
        runtime_identity="codex-test",
    )

    with pytest.raises(ProductError) as error:
        asyncio.run(
            agent.run(
                WorkcellAgentInvocation(
                    delivery_id="delivery-redaction",
                    workcell_run_id="workcell-redaction",
                    agent_run_id="agent-redaction",
                    phase="planning",
                    workcell_key="design",
                    stage_path="design-repair/design",
                    instruction="plan",
                    workspace=tmp_path,
                    workspace_access="none",
                )
            )
        )

    assert error.value.code == "CODEX_WORKCELL_ATTEMPT_FAILED"
    assert credential not in error.value.detail
    assert "not-inherited" not in error.value.detail
    assert error.value.context is not None
    assert error.value.context["stderr_sha256"] == hashlib.sha256(
        b"not-inherited"
    ).hexdigest()


def test_codex_workcell_inherits_process_only_proxy_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://127.0.0.1:7890"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("UNRELATED_PARENT_SETTING", "must-not-be-inherited")
    code = (
        "import json,os,sys; sys.stdin.read(); "
        "payload={'https_proxy':os.environ.get('HTTPS_PROXY'),"
        "'no_proxy':os.environ.get('NO_PROXY'),"
        "'unrelated_present':'UNRELATED_PARENT_SETTING' in os.environ}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-proxy",
                workcell_run_id="workcell-proxy",
                agent_run_id="agent-proxy",
                phase="planning",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="plan",
                workspace=tmp_path,
                workspace_access="none",
            )
        )
    )

    assert output.content == {
        "https_proxy": proxy,
        "no_proxy": "127.0.0.1,localhost",
        "unrelated_present": False,
    }


def test_codex_workcell_agent_filters_shell_metadata_and_non_utf8_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("_", "/tmp/invalid-\udcff-python")
    monkeypatch.setenv("AGENT_TEAM_OS_INVALID_UTF8", "invalid-\udcff-value")
    code = (
        "import json,os,sys; sys.stdin.read(); "
        "payload={'shell_metadata_present':'_' in os.environ,"
        "'invalid_value_present':'AGENT_TEAM_OS_INVALID_UTF8' in os.environ,"
        "'bytecode_disabled':os.environ.get('PYTHONDONTWRITEBYTECODE'),"
        "'valid_marker':os.environ.get('AGENT_TEAM_OS_VALID_MARKER')}; "
        "event={'type':'item.completed','item':{'type':'agent_message',"
        "'text':json.dumps(payload)}}; print(json.dumps(event))"
    )
    agent = CodexWorkcellAgent(
        command=(sys.executable, "-c", code),
        runtime_identity="codex-test",
    )

    output = asyncio.run(
        agent.run(
            WorkcellAgentInvocation(
                delivery_id="delivery-environment",
                workcell_run_id="workcell-environment",
                agent_run_id="agent-environment",
                phase="delegate",
                workcell_key="design",
                stage_path="design-repair/design",
                instruction="review",
                workspace=tmp_path,
                workspace_access="candidate_read",
                method_id="bmad-review",
                environment={"AGENT_TEAM_OS_VALID_MARKER": "preserved"},
            )
        )
    )

    assert output.content == {
        "shell_metadata_present": False,
        "invalid_value_present": False,
        "bytecode_disabled": "1",
        "valid_marker": "preserved",
    }
