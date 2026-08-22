# ADR-0002: Organize Agent-Team-OS as a Modular Monolith

Status: accepted for V0.2.1.

## Decision

Delivery, Evidence, Orchestration, Agents, Board, Identity, Knowledge, and Settings are product
Modules. Each Module presents one application Interface through commands and queries. Domain code
does not import FastAPI, SQLite, ACWM, AgentScope, Hermes, or Codex. Infrastructure Adapters satisfy
explicit Ports at the Module seam. HTTP is an Adapter and contains no state transition logic.

Cross-Module calls use an explicit command/query Interface or committed Product Events. A Module
must not open another Module's tables or Repository. ACWM is reached only through its Gateway; its
runtime contracts are not copied into this repository.

## Consequences

The Interface is the public test surface. Behaviour gains locality inside the owning Module, while
callers gain leverage from a smaller Interface. Architecture tests reject illegal imports before
runtime tests execute.

