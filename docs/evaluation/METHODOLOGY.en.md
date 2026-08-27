# Agent-Team-OS evaluation methodology

## Purpose and evidence boundary

Evaluation is an independent product domain. ACWM still owns cross-stage orchestration, AgentScope
owns stage-local composition, Hermes owns PM/Admin intelligence, Codex owns controlled execution,
and product code owns permissions, evidence, verification and apply policy. Evaluation schedules
cases, collects observations, scores paired subjects and writes immutable reports; it does not copy
an ACWM Runtime Contract or mutate Delivery semantics.

Every run freezes its dataset and scorer SHA-256, Pipeline Revision and fingerprint, deployment
bindings, Git/ACWM revisions, candidate/baseline identities, seed, concurrency, timeout and cost
budget. Deterministic fixture evidence is always `fixture_harness_only` and never an official score.

## Dimensions and scoring

| Dimension | Input and evidence | Scoring | Current offline boundary |
|---|---|---|---|
| ToolCall | Standardized tool traces | AST-normalized exact match; parallel calls use a multiset | Fixture traces only |
| General Agent | Typed final answers | Quasi-exact text, number, date and list match | Project-owned examples only |
| Data Generation | Requirement through Apply Receipt | Blind pairwise wins/ties/losses; independent Judge required | Same-subject tie only |
| Control Plane | Real local GraphRun/SQLite/ASGI operations | Percentiles, status and recovery invariants | No network/TLS/proxy SLA |

`win_rate = wins / (wins + losses)` and `non_loss_rate = (wins + ties) / total`; ties and all
denominators remain visible. Binary accuracy reports a Wilson 95% confidence interval. Human review
includes required failures/conflicts plus fixed-seed sampling; agreement and Cohen's kappa remain
empty until reviews actually exist.

## Profiles and repeatability

| Profile | Base cases | Concurrency | Repetitions | Counted observations |
|---|---:|---|---:|---:|
| smoke | 10 | 1 | 1 | 10 |
| standard | 100 | 1/4/8 | 3 per concurrency; first is warm-up | 600 |
| live | bounded, explicit | 2 by default | runtime-controlled | unavailable until configured |

Standard uses the same dataset, seed and environment for candidate and baseline. Candidate/baseline
probe order is randomized using the recorded seed. Git side effects remain serialized; control
probes use an isolated SQLite database and report work directory.

The ten versioned base cases are expanded deterministically to 100 workloads with `index % 10`,
then shuffled with the recorded seed. Two measured repetitions across concurrency 1/4/8 produce
`100 x 3 x 2 = 600` observations, distributed as 300/180/60/60 by the 5/3/1/1 base-case mix.

## Calibration and gates

Three offline Standard self-comparisons create an immutable Calibration Profile from medians and
median absolute deviations. Runs before calibration are `calibrating`; the next paired run can be
`passed` or fail a gate. Dataset/scorer or subject fingerprint changes create a new calibration
identity, so an older suite cannot silently calibrate a newer suite.

After calibration the gate fails for automatic accuracy/success regression over 2 percentage
points, p95 latency/average cost/tool-call degradation over 20%, error-rate increase over 1 point,
recovery decrease over 2 points, or human-confirmed generation loss rate over 10%. Secret leakage,
invalid evidence hashes, unauthorized effects, wrong apply or forged identity fail immediately.

Status semantics are fail-closed:

- `passed`: all applicable calibrated gates passed;
- `failed`: an applicable correctness, safety, reliability or regression gate failed;
- `calibrating`: evidence exists but three-run calibration is incomplete;
- `blocked`: required runtime, credentials or independent Judge evidence is missing;
- `not_run`: a requested live dimension did not execute and is not a pass;
- `unsupported`: the frozen Runtime lacks a required feature and the case is excluded from scores.

When Live Runtime is unavailable, Case status is `blocked`, EvaluationRun status is `blocked`, and
the EvaluationReport gate is `not_run`. The report conclusion and execution states are separate.

## Dataset lifecycle

The canonical dataset lives under `evaluation/datasets/<suite>/<version>`. Manifest hashes lock the
JSONL cases and JSON Schema. Stable Case IDs, exact dimension counts and dimension-specific scoring
contracts are validated before a run. Missing files, duplicate IDs, schema drift, changed bytes or
distribution mismatch stop the run.

Any case, expected output, scoring rule or schema change requires:

1. a new dataset version and scorer compatibility decision;
2. regenerated file hashes in `manifest.json`;
3. updated dataset/card and contract tests;
4. three fresh Standard calibration runs;
5. a new sanitized baseline only after evidence review.

Official BFCL/GAIA data must use a separately licensed, pinned dataset and official scorer identity.
Project-owned compatible cases must never be renamed to official benchmark results.

The published suite 1.2.0 baseline is historical: its cases were embedded in code and no replayable
1.2.0 dataset directory is provided. Repeatable validation starts with versioned suite 1.3.0; a
1.3.0 result must not be described as an exact replay of 1.2.0.

## Reproduction and CI

Validate the dataset and run locally against an initialized product database:

```bash
uv run agent-team-os-dev eval validate-dataset
uv run agent-team-os-dev eval run --mode offline --profile smoke --seed 20260824
```

[PR/Push CI](../../.github/workflows/ci.yml) runs dataset/schema/hash contracts, Ruff, Mypy, Pytest,
migration checks and build only. [Manual evaluation](../../.github/workflows/evaluation.yml) uses an
explicit temporary `AGENT_TEAM_OS_DATA_DIR` and `--bootstrap-fixture`; the flag refuses the default
product data directory. It runs three calibration rounds followed by a fourth run with
`--require-gate-passed`, then uploads JSON/Markdown reports, the SQLite ledger and calibration
evidence. The workflow's default seed is `20260824`.

Live execution must be explicitly added later with real Runtime identity, credentials, token/cost
budgets and network dependencies. Until then Case and EvaluationRun status are `blocked`, the
EvaluationReport gate is `not_run`, and the missing Live result does not block the offline release
gate.

[Chinese default](METHODOLOGY.md)
