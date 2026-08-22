# ADR-0009: Multi-Pipeline Control Plane Uses ACWM DAG and Bounded Loop Semantics

Status: accepted

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
- The existing `backend-delivery` Journey migrates to a one-path graph and retains Reject, Accept,
  candidate verification and CAS apply guarantees.
- New Deliveries must select an active Published Pipeline Revision. There is no implicit default
  once migration is complete.

## Delivery order

1. ACWM graph definition and compiler.
2. Pipeline Catalog and immutable revisions.
3. Graph Execution Ledger and recovery.
4. DAG/Loop editor and runtime projections.
5. deterministic and live release gates.

## Consequences

The current linear editor and hard-coded six-step Operating Map are transitional. They remain
available only while the built-in Pipeline is migrated and must not be presented as the final
orchestration capability.
