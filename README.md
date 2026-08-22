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

## V0.1 delivery baseline

Implemented:

- create a Backend Delivery and stop at plan approval;
- optimistic-version protection for decisions;
- execute only after plan approval and expose immutable candidate evidence;
- persist and recover Delivery snapshots through SQLite;
- reject a candidate without an apply side effect;
- verify a candidate independently and require an exact atomic-apply receipt before completion;
- fail-closed readiness for ACWM, AgentScope, Hermes credentials and Codex login;
- pin ACWM v0.3 to commit `b79e671`.
- resolve and fingerprint the authoritative ACWM `backend-delivery` Journey;
- return `202` immediately and advance planning/execution/apply in background tasks;
- bind both approvals to ACWM Gate Subject hashes and optimistic versions;
- execute Codex with `workspace-write` in an isolated Git Worktree;
- reject empty, out-of-scope, secret-bearing or test-failing candidates;
- create immutable candidate refs, unified Diff hashes and fixed unittest evidence;
- apply only with `git update-ref <candidate> <base>` compare-and-swap;
- recover approval states after restart and fail interrupted execution closed;
- expose Delivery history, cancellation, sandbox reset and release-gate reports.

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

### Run the product

The product uses Codex to simulate Hermes PM/Admin through AgentScope and ACWM. Code execution is a
real Codex CLI workspace-write turn. The target is the built-in standard-library Python Backend Bare
Repo under `.agent-team-os/workspaces`; user repositories are intentionally out of scope for V0.1.

```bash
uv sync --extra dev --extra live
uv run --extra live agent-team-os demo
```

Open <http://127.0.0.1:8080/>. Data and immutable reports are stored under `.agent-team-os/`.

### Release gates

```bash
# Real Git lifecycle with deterministic model boundaries
uv run --extra live agent-team-os gate

# Real Codex planning and real Codex code execution
uv run --extra live agent-team-os gate --live

# Run both gates; either failure returns a non-zero exit code
uv run --extra live agent-team-os release
```

Both JSON and Markdown reports include DEV/ACWM revisions, Candidate Revision, Diff SHA-256,
verification exit code, identities and an evidence hash. `/v1/release-gates/latest` reports
`unknown` or `failed` when evidence is missing, older than 24 hours, dirty, or revision-mismatched.
