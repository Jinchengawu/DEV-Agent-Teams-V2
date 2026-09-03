# ADR-0009: Multi-Pipeline Control Plane Uses ACWM DAG and Bounded Loop Semantics

Status: accepted

Latest reconciliation: 2026-09-04

## Context

The V0.2 control plane can persist more than one Journey id, but creation defaults to
`backend-delivery`, the editor derives a linear order from canvas coordinates, and Delivery owns a
single linear state. This does not meet the original requirement for multiple configurable
Pipelines with branching, joining and looping.

## Decision

Agent-Team-OS will expose a Pipeline Catalog whose immutable Published Revisions pin an ACWM v0.4
compiled Journey Graph and Capability binding snapshot.

- ACWM is the authority for graph definition, topology validation, deterministic compilation,
  cross-Stage execution semantics and bounded Loop accounting.
- Agent-Team-OS owns Pipeline Draft/Revision lifecycle, activation, permissions, business inputs,
  Product Events, evidence, approval commands and final side effects.
- The outer graph is a DAG. A Loop is an explicit Node with an acyclic body, maximum iterations,
  timeout, exit-condition policy and exhaustion action. Arbitrary back edges are rejected.
- A Pipeline Run records per-Node Runs, Attempts and Loop Iterations. Board and Operating Map are
  rebuildable projections of committed facts.
- React Flow edges edit semantic dependencies. Canvas position remains presentation-only.
- The existing `backend-delivery` Journey migrates to a schema-v4 graph whose code-delivery Stage
  is inside a bounded repair Loop, while retaining Reject, Accept, candidate verification and CAS
  apply guarantees.
- New Deliveries must select an active Published Pipeline Revision. There is no implicit default
  once migration is complete.
- Built-in Pipeline import is a bootstrap default, not an authority over an operator-published
  active Revision. Startup may automatically migrate only an active Revision published by the
  same bootstrap actor. If another actor published the active Revision, startup preserves it and
  must not patch its draft, publish a duplicate fingerprint, or reactivate an older built-in
  definition.
- A Pipeline Revision is identified by `(pipeline_id, revision)`. Its `fingerprint` is the ACWM
  compiled graph integrity value, not a global Revision identity or deduplication key. The same
  graph may therefore be republished as a new immutable Revision when frozen Provider Bindings,
  Workcell contracts, Knowledge context bindings, policy snapshots or presentation metadata
  change. Publication must preserve both Revisions instead of rejecting the new snapshot or
  silently reusing the previous one.

## Delivery order

1. ACWM graph definition and compiler.
2. Pipeline Catalog and immutable revisions.
3. Graph Execution Ledger and recovery.
4. DAG/Loop editor and runtime projections.
5. deterministic and live release gates.

## Consequences

Independent ready AgentScope role-turn Stages may execute concurrently. Git-mutating code-delivery
Stages remain serialized within one Delivery workspace so that Candidate and CAS evidence retain a
single unambiguous ancestry. Nested human approval Gates inside a Loop are rejected until the
product owns a durable nested-Gate record and recovery contract.

Operator-published Pipeline Revisions therefore survive process restart without silent downgrade.
Changing such a Revision remains an explicit Draft/Validate/Publish/Activate operation; a future
built-in definition cannot take ownership merely because application code was upgraded or rolled
back.

Removing global uniqueness from `pipeline_revisions.fingerprint` does not weaken graph integrity:
each Revision still freezes and exposes that fingerprint, while the composite primary key remains
the consistency boundary for lookup, activation, Delivery Snapshot compilation and audit history.
