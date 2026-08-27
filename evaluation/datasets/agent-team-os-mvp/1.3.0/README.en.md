# Agent-Team-OS MVP evaluation dataset 1.3.0

This project-owned synthetic dataset validates the evaluation harness and local control-plane
probes. It is not an official BFCL or GAIA dataset and must not be used to claim leaderboard or
live-agent performance.

## Contents and intended use

- five BFCL-compatible tool-call cases;
- three GAIA-compatible typed-answer cases at difficulty levels 1/2/3;
- one pairwise full-chain generation contract;
- one local GraphRun/SQLite/HTTP recovery probe contract.

`fixture_output` is deliberately labelled deterministic and is consumed only in `offline` mode.
Live execution strips it before scheduling. Until a real runtime is configured, Case and
EvaluationRun status are `blocked`, while the EvaluationReport gate is `not_run`. Standard expands
these ten cases with a fixed seed across concurrency 1/4/8 and keeps two measured repetitions after
one warm-up repetition.

Expansion cycles the ten base cases to 100 workloads with `index % 10`, then shuffles them with the
recorded seed. Standard therefore records `100 x 3 concurrency levels x 2 measured repetitions =
600` observations.

## Provenance, license and versioning

The cases are authored for Agent-Team-OS and released under the repository license. Any semantic
case, expected answer, scoring rule or schema change requires a new dataset version, updated file
hashes in `manifest.json`, scorer compatibility tests and a fresh three-run calibration profile.

[Chinese default](README.md)
