# Agent-Team-OS Domain Context

Agent-Team-OS is a delivery control plane. It turns an approved Backend requirement into a
candidate Git change, machine verification, an explicit human decision, and an atomic apply
receipt. A successful label is never evidence by itself.

## Domain language

- **Delivery**: the product Aggregate that owns one request from planning through apply or a
  terminal failure/rejection.
- **Journey Revision**: the immutable ACWM definition and Capability binding snapshot selected for
  a Delivery.
- **Gate**: an ACWM approval decision bound to one Artifact hash and optimistic Revision.
- **Candidate**: an immutable Git commit created from a recorded Base Revision.
- **Evidence Record**: an append-only product fact that references immutable content, its source,
  producing identity, and verification status.
- **Apply Receipt**: proof that Main moved from the recorded Base Revision to the reviewed
  Candidate Revision.
- **Product Event**: a committed product fact used by projections and SSE. AgentScope messages and
  provider-private state are not Product Events.
- **Work Item**: a rebuildable Board projection of a Delivery. It never owns Delivery state.
- **Wiki Document**: user-authored, versioned knowledge. It is distinct from immutable delivery
  evidence even when both are searchable.
- **Agent Instance**: a deployable Hermes or Codex runtime registration containing references to
  credentials, never credential values.

## Ownership

- ACWM owns cross-Stage Journey, Capability/Workflow resolution, Handoff, and global Gates.
- AgentScope owns Stage-local messages, sessions, memory, and role composition.
- Hermes owns PM and Project Admin role intelligence.
- Codex owns controlled code execution in an isolated workspace.
- Agent-Team-OS owns business state, permissions, Git policy, Evidence Records, decisions, and
  final side effects.

## Non-negotiable invariants

- Deterministic adapters and live agents have different evidence identities.
- Missing, all-zero, stale, or unverifiable hashes are not valid evidence.
- Reject never changes Main; Accept succeeds only when Main equals the reviewed Candidate.
- A Board move expresses a command; it cannot write a terminal state directly.
- Published Journey Revisions and Evidence Records are immutable.
- Secrets are represented only by environment or system credential references.

