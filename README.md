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

## V0.3 multi-Pipeline DAG/LOOP release candidate

Implemented on top of the V0.2 control plane:

- create and retain multiple independently configurable Pipelines;
- edit semantic DAG dependencies and conditional edges with React Flow;
- validate topology and product compatibility before publishing immutable revisions;
- activate a selected revision and pin every new graph Delivery to its definition, binding snapshot
  and SHA-256 fingerprint;
- execute the authoritative ACWM GraphRun instead of translating the graph back into a fixed
  Delivery sequence;
- run independent ready AgentScope role Stages concurrently while serializing Git side effects;
- execute code delivery inside a bounded repair LOOP with per-iteration and per-body-node evidence;
- retain immutable candidate refs for every code attempt and compute the final base-to-candidate
  Diff;
- recover completed GraphRuns after restart and fail/cancel graph execution closed;
- require the deterministic and live release gates to attest Pipeline Revision, graph fingerprint,
  completed GraphRun, Candidate, Diff, verification and restart evidence.

The built-in `backend-delivery` definition is now a schema-v4 DAG with a maximum-three-iteration
code repair LOOP. Product compatibility requires PM, Project Admin and Backend capabilities plus
exactly one plan Gate and one candidate Gate. Nested human Gates inside LOOP bodies are currently
rejected explicitly.

The ACWM v0.4 source commit used by this release candidate must be published before the dependency
lock can move from the previous v0.3 revision. Until that release action is authorized, this branch
is validated against the sibling ACWM v0.4 checkout and is not yet a reproducible fresh-clone
release.

## V0.2 control-plane baseline

Implemented:

- preserve the V0.1 real Git delivery loop and its CAS apply guarantees;
- register `hermes-acp`, `hermes-http`, and `codex-cli` execution instances without persisting secret values;
- health-check instances and bind healthy, enabled instances to ACWM capabilities;
- clone, reorder, validate, and publish ACWM Journeys as immutable revisions;
- pin every new Delivery to a published Journey revision and frozen binding snapshot;
- project Delivery state into a rebuildable six-column Board with legal commands only;
- archive Journey, requirement, task, gate, candidate, verification, and receipt evidence;
- search traceable knowledge with SQLite FTS5, content hashes, and source links;
- expose a durable control event stream at `/v1/events/stream`;
- serve a React/Vite control console with a persistent Operating Map.

The control plane deliberately does not implement RAG, embeddings, AgentScope-native Agent/Team
management, shared long-term memory, multi-tenancy, cloud deployment, or user repository adapters.
Those remain later milestones.

V0.1 guarantees retained:

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

cd console
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/vitest/vitest.mjs run
node ./node_modules/vite/bin/vite.js build

# Explicit real-Codex smoke test (read-only planning; requires Codex login)
AGENT_TEAM_OS_LIVE_CODEX=1 uv run pytest \
  tests/integration/test_live_codex_simulated_planning.py -q
```

### Run the product

The product uses Codex to simulate Hermes PM/Admin through AgentScope and ACWM. Code execution is a
real Codex CLI workspace-write turn. The target is the built-in standard-library Python Backend Bare
Repo under `.agent-team-os/workspaces`; user repositories are intentionally out of scope for V0.2.

```bash
uv sync --extra dev --extra live
pnpm --dir console install --frozen-lockfile
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

Both JSON and Markdown reports include DEV/ACWM revisions, Pipeline Revision, graph fingerprint,
GraphRun identity/status, Candidate Revision, Diff SHA-256, verification exit code, identities and
an evidence hash. The `release` command returns success only
when both gates are clean and refer to the same DEV and ACWM revisions. The Settings page and
`/v1/release-gates/latest` report `unknown` or `failed` when the newest evidence is missing, corrupt,
older than 24 hours, hash-invalid, identity-invalid, incomplete, or revision-mismatched. The
deterministic report must also prove the full browser loop and process-restart recovery.
