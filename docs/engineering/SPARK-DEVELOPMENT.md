# Codex Implementation Worker Contract

The implementation worker factory is a development-time Adapter for focused, repetitive
implementation after architecture and public contracts are frozen. It is not a product runtime
Agent Instance. The historical `spark` CLI and storage names remain for compatibility; every Task
records the exact Codex model that actually performed the work.

## Definition of ready

A worker Task has an exact Base Revision, architecture Revision, exact supported model, allowed and forbidden paths,
referenced contracts, test commands, and decision-complete acceptance criteria. It changes no
dependency, migration, public contract, state machine, authorization rule, evidence rule, Git apply
rule, or framework Adapter.

## Isolation and review

Each Task runs with the model named by its manifest in `codex/spark/<task-id>` and an isolated
Worktree. Supported identities are explicitly allowlisted; currently they are
`gpt-5.3-codex-spark` and `gpt-5.6-sol`. The runner passes that exact identity to Codex, records it,
and rejects identity drift. It checks the Diff, paths, secrets, dependencies, and fixed tests. It
may create a candidate commit but never merges or pushes. A primary architecture review accepts or
rejects the candidate.

Tracked Task and Review files contain metadata and conclusions. Raw model events remain under
`.agent-team-os/spark-runs/` and are not committed.

## Fail closed

Missing decisions return `blocked/ARCHITECTURE_DECISION_REQUIRED`. Model unavailability, a changed
Base Revision, forbidden paths, dependency changes, warnings, skipped tests, or failed verification
make the Task unacceptable. No model fallback is permitted.
