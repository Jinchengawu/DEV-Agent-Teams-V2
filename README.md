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
- fail-closed readiness for ACWM, AgentScope, Hermes credentials and Codex login;
- pin ACWM v0.3 to commit `b79e671`.

Candidate acceptance is intentionally unavailable until machine verification and atomic apply ports
exist. Deterministic adapters are test-only and identify their evidence as `deterministic-test`.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
uv build
```
