# ADR-0001: Clean-room Agent-Team-OS V2

## Status

Accepted.

## Decision

Build V2 as a Python 3.11 service in a new repository. ACWM v0.3 is the thin cross-stage control
plane. AgentScope owns stage-local communication. Hermes PM and Project Admin are capability
nodes, while Codex is the only initial code executor.

The first release supports one built-in Backend workspace and one active Delivery at a time. No
legacy Workflow, chat, RAG, multi-tenant or frontend-execution code is migrated.

## Consequences

Every successful delivery must have independently traceable planning artifacts, candidate
evidence, verification evidence, user decision and apply receipt. Deterministic test adapters and
live runtime adapters produce different evidence identities.

During bootstrap, Codex may simulate Hermes PM and Project Admin behind the same AgentScope/ACWM
boundary. This compatibility path uses the explicit `codex-simulated-hermes` evidence identity and
does not satisfy a future live-Hermes release gate.
