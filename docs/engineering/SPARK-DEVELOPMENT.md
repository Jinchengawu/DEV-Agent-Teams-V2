# Codex Spark Development Contract

Codex Spark is a development-time Adapter for focused, repetitive implementation after architecture
and public contracts are frozen. It is not a product runtime Agent Instance.

## Definition of ready

A Spark Task has an exact Base Revision, architecture Revision, allowed and forbidden paths,
referenced contracts, test commands, and decision-complete acceptance criteria. It changes no
dependency, migration, public contract, state machine, authorization rule, evidence rule, Git apply
rule, or framework Adapter.

## Isolation and review

Each Task runs as `gpt-5.3-codex-spark` in `codex/spark/<task-id>` and an isolated Worktree. The
runner checks the Diff, paths, secrets, model identity, and fixed tests. It may create a candidate
commit but never merges or pushes. A primary architecture review accepts or rejects the candidate.

Tracked Task and Review files contain metadata and conclusions. Raw model events remain under
`.agent-team-os/spark-runs/` and are not committed.

## Fail closed

Missing decisions return `blocked/ARCHITECTURE_DECISION_REQUIRED`. Model unavailability, a changed
Base Revision, forbidden paths, dependency changes, warnings, skipped tests, or failed verification
make the Task unacceptable. No model fallback is permitted.

