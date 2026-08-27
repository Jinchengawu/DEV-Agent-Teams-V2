# DEV-Agent-Teams V2 / Agent-Team-OS

> This is the English companion. [Chinese default](README.md)

Agent-Team-OS is the clean-room V2 implementation of DEV-Agent-Teams and is a delivery control
plane, not a multi-agent chat UI.

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

- ACWM owns cross-stage Journey, Workflow/Capability resolution and global Gates.
- AgentScope owns communication and agent composition inside a Stage.
- Hermes owns PM and Project Admin role intelligence.
- Codex owns controlled code execution.
- Agent-Team-OS owns workspace security, evidence, policy, decisions and product APIs.

V2 is a new project. The legacy DEV-Agent-Teams repository remains untouched and is not a source
tree to copy.

## V0.3 multi-Pipeline DAG/LOOP release candidate

The V0.3 control plane can create multiple Pipelines, edit and validate semantic DAGs, publish and
activate immutable Pipeline Revisions, freeze binding snapshots and fingerprints, execute the
authoritative ACWM GraphRun, run independent ready Stages concurrently while serializing Git side
effects, retain per-iteration repair LOOP evidence, recover GraphRuns after restart, and fail
closed when release evidence is missing or inconsistent.

The built-in `backend-delivery` Pipeline is a Schema-v4 DAG with a maximum-three-iteration code
repair LOOP. It requires PM, Project Admin and Backend capabilities, exactly one Plan Gate and one
Candidate Gate. Nested Human Gates inside LOOP bodies are rejected.

## V0.2 and V0.1 guarantees

The project retains the real Git delivery lifecycle and CAS Apply guarantees, durable SQLite
Delivery snapshots, immutable Candidate and Evidence identities, independent verification, exact
Apply Receipts, background planning/execution/apply, restart recovery, a rebuildable Board,
versioned knowledge/provider snapshots, a durable event stream, and fail-closed readiness checks.

Codex may simulate Hermes PM/Admin until Hermes Instances are configured. Such evidence is always
identified as `codex-simulated-hermes`, never as a real Hermes call. Deterministic adapters remain
test-only and use the `deterministic-test` evidence identity.

## Project evaluation

Evaluation freezes the Pipeline Revision, Deployment bindings, Git/ACWM revisions, dataset,
scorer, seed and environment. Dimensions are reported separately; no aggregate score is emitted.

Latest published historical baseline: **2026-08-24, suite 1.2.0, offline standard, seed 20260824**.

| Dimension | Evaluated/total | Result | Evidence boundary |
|---|---:|---:|---|
| ToolCall / BFCL-compatible | 300/300 | 300 passed | Fixture AST/Trace scoring |
| General Agent / GAIA-compatible | 180/180 | 180 passed | Fixture quasi-exact scoring |
| Data Generation | 60/60 | 60 ties | Same frozen subject only |
| Control Plane | 60/60 | 60 passed | Local GraphRun/SQLite/ASGI probes |

Candidate HTTP latency was p50 2.36 ms, p95 6.29 ms and p99 9.43 ms. Candidate GraphRun total
latency was p50 8.80 ms, p95 101.03 ms and p99 121.81 ms.

- Gate: `passed`; proof scope: `fixture_harness_only`; official benchmark: `false`.
- Evidence SHA-256: `d9e2019fa6e86f632e0d3d513f04cf7a73d3de55065e05f845919080ead3e2c6`.
- The historical suite 1.2.0 cases were code-embedded and are not represented as a replayable
  versioned dataset. Repeatable validation now uses suite 1.3.0.
- These fixtures do not prove live-agent ability, official BFCL/GAIA results, independent
  generation quality, token/cost behavior or production network SLA.

See the [English methodology](docs/evaluation/METHODOLOGY.en.md),
[dataset card](evaluation/datasets/agent-team-os-mvp/1.3.0/README.en.md),
[historical baseline](docs/evaluation/results/2026-08-24-offline-standard.en.md),
[PR CI](.github/workflows/ci.yml) and [manual evaluation](.github/workflows/evaluation.yml).

## Development validation

```bash
uv sync --extra dev
uv run agent-team-os-dev eval validate-dataset
uv run pytest
uv run ruff check .
uv run mypy
uv build

cd console
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/vitest/vitest.mjs run
node ./node_modules/vite/bin/vite.js build
```

## Run the product

```bash
uv sync --extra dev --extra live
pnpm --dir console install --frozen-lockfile
uv run --extra live agent-team-os demo
```

Open <http://127.0.0.1:8080/>. Data and immutable reports are stored under `.agent-team-os/`.

## Release gates

```bash
uv run --extra live agent-team-os gate
uv run --extra live agent-team-os gate --live
uv run --extra live agent-team-os release
```

Reports contain DEV/ACWM and Pipeline revisions, graph fingerprint, GraphRun identity/status,
Candidate Revision, Diff SHA-256, verification result, runtime identities and an Evidence Hash.
Release succeeds only when deterministic and live evidence are both valid and revision-aligned.
