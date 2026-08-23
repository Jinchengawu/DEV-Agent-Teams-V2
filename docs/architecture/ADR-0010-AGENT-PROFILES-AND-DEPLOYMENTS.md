# ADR-0010: Separate Agent Profiles from Runtime Deployments

Status: accepted for V0.3.1

## Context

The V0.3 instance registry combines a display name, runtime connection and browser-submitted
Feature list. It cannot express reusable frontend, testing or planning roles, and self-reported
Features are not trustworthy enough for Pipeline publication.

ACWM v0.5 now owns content-addressed Capability Provider Manifests, Artifact Contracts and Stage
binding-site resolution. Agent-Team-OS must use those contracts without recreating a parallel
runtime invocation protocol.

## Decision

The product uses this authority chain:

```text
AgentProfileSpec                   Agent-Team-OS
  -> CapabilityProviderManifest   ACWM
  -> AgentDeployment              Agent-Team-OS
  -> ResolvedProviderBinding      ACWM
  -> AgentScope/Hermes/Codex      execution
```

`AgentProfileSpec` is a reusable logical role. Its Draft uses optimistic CAS; an administrator
publishes immutable, canonical JSON revisions with SHA-256. Equivalent JSON and YAML produce the
same canonical content. A Profile may reference Prompt, Tool, Resource, Approval, Memory and
Delegation policies but never embeds endpoints, credentials, workspace paths, Artifact schemas or
trusted runtime Features.

`AgentDeployment` is environment-local. It binds one Profile Revision to one Runtime Instance and
freezes an ACWM Provider Manifest fingerprint, Adapter version, isolation mode and effective policy
snapshots. Qualification fails closed when a Profile, Instance, Adapter, Capability, Artifact or
permission requirement is stale or incompatible.

One Runtime Instance may serve multiple shared Deployments. A `dedicated` Profile may not share an
Instance. Session/workspace isolation remains an execution responsibility and must produce
evidence; this milestone does not implement a long-term Memory kernel or a Team abstraction.

Runtime Adapter Features are read from installed ACWM Adapter Manifests and refreshed by health
checks. Create and Patch APIs reject browser-submitted Features. Historical self-reported values
remain auditable data but are ignored for new bindings and qualifications.

Pipeline Drafts assign Deployments at `Stage path + Slot`; ACWM Journey bindings continue to name
Capabilities. Publication freezes Agent Revision, Runtime Instance/Adapter, Provider Manifest,
Artifact Contracts, effective policies and the `ResolvedProviderBinding` hash. Runtime execution
continues to use ACWM `resolve -> stage -> signal`.

## Consequences

- Existing `/v1/capability-bindings` remains a read-compatible migration surface; new Pipeline
  revisions do not use it as a global singleton.
- A local Codex CLI Deployment cannot export an A2A Agent Card because it has no network endpoint.
- Agent Profile MCP fields are catalog URI references; the product does not copy MCP schemas or
  accept arbitrary MCP Server uploads in V0.3.1.
- Agent Profile revisions are globally portable; instances, credentials, qualification and
  workspace policy remain local to one environment.
