# Agent-Team-OS Domain Context

> Canonical architecture entry point: [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md).
> This file remains the domain-language and invariant glossary; it does not duplicate the current
> architecture snapshot or its accepted-change ledger.

Agent-Team-OS is a delivery control plane. It turns an approved Backend requirement into a
candidate Git change, machine verification, an explicit human decision, and an atomic apply
receipt. A successful label is never evidence by itself.

## Domain language

- **Delivery**: the product Aggregate that owns one request from planning through apply or a
  terminal failure/rejection.
- **Journey Revision**: the immutable ACWM definition and Capability binding snapshot selected for
  a Delivery.
- **Pipeline**: a product-managed identity with Draft, immutable Published Revisions, activation,
  permissions and an input contract. Each Published Revision references one compiled ACWM Journey
  Graph.
- **Pipeline Run**: one durable execution of a Published Pipeline Revision. It projects ACWM Node
  Runs, Attempts and Loop Iterations while product state retains approvals, evidence and final side
  effects.
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
- **Knowledge Provider Binding**: a versioned local link to one external collaborative knowledge
  space. It stores provider identifiers and credential references, never credential values.
- **Provider Snapshot**: an immutable, normalized and content-addressed local observation of one
  external knowledge document at a recorded Provider Revision.
- **Provider Sync Run**: the auditable command result that links a Binding, source, Provider
  Revision, Snapshot hash and stable failure state.
- **Agent Instance**: a deployable Hermes or Codex runtime registration containing references to
  credentials, never credential values.
- **Agent Profile**: a globally reusable, versioned logical role containing instructions,
  Capability requirements and policy references. It contains no endpoint, credential, trusted
  runtime Feature, workspace path or Pipeline assignment.
- **Agent Deployment**: an environment-local qualification of one immutable Agent Profile
  Revision against a Runtime Instance, ACWM Provider Manifest and effective policy snapshots.
- **Runtime Adapter**: an installed ACWM Adapter Manifest inspected by the product. Its Features
  are runtime truth and cannot be submitted by the browser.
- **Agent Assignment**: one Pipeline Stage binding site mapped to a qualified Deployment. Published
  Pipeline Revisions freeze the resulting ACWM Resolved Provider Binding.
- **Workcell Composition**: the product-owned, observable set of Main and Child AgentRuns for one
  WorkcellRun, including their parentage, purpose, access and lifecycle. _Avoid_: AgentScope team
  composition.
- **Attempt Runtime**: the AgentScope-owned Stage-local session, message and runtime transport used
  inside one product-created AgentAttempt. It cannot create a new AgentRun identity.
- **Hidden Child**: a runtime-spawned child without a product-owned AgentRun and AgentAttempt record;
  this concept is prohibited by the Workcell boundary.
- **Project Repository Set**: the immutable project-scoped collection of Backend, Frontend, Design
  and QA repository identities frozen when a full-stack Delivery starts.
- **Release Bundle**: the reviewed collection of per-repository Base, Candidate, Diff and
  Verification facts coordinated as one product release.
- **Release Manifest**: the immutable product authority naming the exact repository revisions that
  form one accepted release. Git Main refs must converge to it but are not cross-repository atomic.
- **Release Acceptance Report**: a content-addressed, read-only verification of one completed
  Delivery's frozen Build Identity, Knowledge Context, observable AgentAttempts, Release Bundle,
  Apply Receipts and active Release Manifest. It never owns Apply or Delivery state.

## Ownership

- ACWM owns cross-Stage Journey, Capability/Workflow/Provider/Artifact compatibility, immutable
  Resolved Provider Bindings, Handoff, and global Gates.
- Agent-Team-OS Workcell Execution owns observable Workcell Composition, Main/Child scheduling,
  cancellation, timeout and lifecycle.
- AgentScope owns Attempt Runtime inside a product-created AgentAttempt; it does not own Workcell
  Composition and cannot create Hidden Children.
- Hermes owns PM and Project Admin role intelligence.
- Codex owns controlled code execution in an isolated workspace.
- Agent-Team-OS owns business state, permissions, Git policy, Evidence Records, decisions, and
  final side effects.

## Non-negotiable invariants

- Deterministic adapters and live agents have different evidence identities.
- Missing, all-zero, stale, or unverifiable hashes are not valid evidence.
- Reject never changes Main; Accept succeeds only when Main equals the reviewed Candidate.
- A multi-repository Delivery is complete only when every Main equals its reviewed Candidate and
  the active Release Manifest names the same revisions; cross-repository Git updates are never
  described as physically atomic.
- A Board move expresses a command; it cannot write a terminal state directly.
- Published Journey Revisions and Evidence Records are immutable.
- Published Pipeline Revisions pin the compiled Journey Graph, Capability bindings, policies and
  graph fingerprint; a running Pipeline never silently resolves a newer definition.
- The outer Journey Graph is acyclic. Repetition is allowed only through an explicit bounded Loop
  Node with auditable iterations and a deterministic exhaustion outcome.
- Secrets are represented only by environment or system credential references.
- Runtime Features come only from installed ACWM Adapter Manifests and health probes; stored
  historical self-reported Features are never trusted for new qualification.
- Every Main and Child is created by Agent-Team-OS before execution and has a product-visible
  AgentRun/AgentAttempt identity; Runtime adapters cannot create hidden descendants.

## Example dialogue

**Developer**: Can AgentScope spawn two reviewers inside its own session?

**Domain expert**: No. Workcell Execution first creates two Child AgentRuns and their AgentAttempts;
AgentScope may transport each Attempt's Stage-local session and messages, but it cannot create a
Hidden Child.
