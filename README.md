# DEV-Agent-Teams V2 / Agent-Team-OS

This repository is the clean-room V2 implementation of DEV-Agent-Teams.

The first product loop is deliberately narrow:

```text
Backend request
  -> Hermes PM requirements
  -> Hermes Project Admin task contract
  -> approval gate
  -> Codex candidate change
  -> product verification
  -> approval gate
  -> atomic apply or reject
```

Architecture ownership:

- ACWM: cross-stage Journey, Workflow/Capability resolution and global Gates.
- AgentScope: communication and agent composition inside a Stage.
- Hermes: PM and Project Admin role instances.
- Codex: code execution capability.
- Agent-Team-OS: workspace security, evidence, policy, decisions and product API.

V2 is a new project. The legacy DEV-Agent-Teams remains untouched and is not a source tree to copy.

## Current baseline

Implemented in the first vertical slice:

- create a Backend Delivery and stop at plan approval;
- optimistic-version protection for decisions;
- execute only after plan approval and expose immutable candidate evidence;
- persist and recover Delivery snapshots through SQLite;
- reject a candidate without an apply side effect;
- verify a candidate independently and require an exact atomic-apply receipt before completion;
- fail-closed readiness for ACWM, AgentScope, Hermes credentials and Codex login;
- pin ACWM v0.3 to commit `b79e671`.

Until Hermes instances are configured, Codex may simulate the PM and Project Admin roles through an
AgentScope role-turn and ACWM's Codex Capability Adapter. Such evidence is always identified as
`codex-simulated-hermes`; it is never reported as a real Hermes call. Invalid structured planning is
retried once and then fails closed. Deterministic adapters remain test-only and identify their
evidence as `deterministic-test`.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
uv build

# Explicit real-Codex smoke test (read-only planning; requires Codex login)
AGENT_TEAM_OS_LIVE_CODEX=1 uv run pytest \
  tests/integration/test_live_codex_simulated_planning.py -q
```

### Start the interactive Preview

The Preview uses Codex to simulate Hermes PM/Admin through AgentScope and ACWM. Candidate execution,
verification and apply are deterministic preview adapters and do not modify a real repository.

```bash
uv sync --extra dev --extra live
uv run --extra live agent-team-os
```

Open <http://127.0.0.1:8080/>. Preview data is stored under `.agent-team-os/`.
