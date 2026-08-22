# ADR-0008: Feishu Is a Collaborative Knowledge Provider, Not the Evidence Authority

Status: accepted for a V0.2.1 proof of concept after the local Wiki golden slice.

## Context

Agent-Team-OS needs a mature collaborative editing experience, comments, document permissions,
and shared knowledge spaces. Feishu already provides these user-facing capabilities. The product
also has requirements that cannot be delegated to an embedded editor: Delivery provenance,
immutable Evidence, content-addressed snapshots, permission-filtered Agent retrieval, and local
audit recovery.

## Decision

Use a hybrid provider model:

- Feishu is the content authority for explicitly linked `manual knowledge` documents.
- Agent-Team-OS is the authority for provider bindings, source provenance, normalized snapshots,
  SHA-256 integrity, synchronization state, Agent-use policy, and the derived local search index.
- Agent-Team-OS remains the sole authority for Delivery Evidence. Evidence may be published to
  Feishu as a clearly marked read-only copy, but Feishu content can never overwrite Evidence.
- The web console embeds documents through the official Feishu Docs Component and authorization
  flow. A raw knowledge-base URL in an unrestricted iframe is not an accepted integration.
- The local Wiki remains a supported provider and the offline/default implementation. Feishu is
  additive and must not become a startup dependency for Delivery, Board, Orchestration, or
  Evidence.

## Boundary

The product-facing port is provider-neutral:

```python
class KnowledgeProvider(Protocol):
    def list_spaces(self, actor: ProviderActor) -> tuple[ProviderSpace, ...]: ...
    def list_nodes(self, actor: ProviderActor, space_id: str) -> tuple[ProviderNode, ...]: ...
    def fetch_snapshot(self, actor: ProviderActor, source_id: str) -> ProviderSnapshot: ...
    def create_embed_grant(self, actor: ProviderActor, source_id: str) -> EmbedGrant: ...
```

Provider DTOs contain external IDs, revisions, MIME/content type, normalized content, source URL,
and provider timestamps. They do not expose Feishu SDK objects outside Infrastructure.

The Feishu adapter belongs under `infrastructure/feishu/`. The Knowledge module owns bindings,
snapshots, sync commands, source policy, and Product Events. Neither side may write Delivery or
Evidence repositories directly.

## Trust and permission rules

- Application secrets and user tokens are referenced by `env:` or `keychain:` only; plaintext
  credentials never enter API responses, logs, SQLite, Evidence, or Spark artifacts.
- A provider document is searchable only when both Feishu access and Agent-Team-OS source policy
  permit the current user.
- Every successful fetch creates or reuses an immutable normalized snapshot identified by
  SHA-256. Provider revision, fetch time, source ID, and actor identity are retained.
- Revoked or failed access makes the source unavailable; stale cached content is not silently
  represented as current.
- Webhook/event input is untrusted. Signature, replay window, tenant/app identity, and event ID
  idempotency are verified before enqueueing synchronization.
- An embedded editor never receives a long-lived application secret. Embed grants are scoped and
  short-lived.

## Product events

The adapter may cause only committed Knowledge events:

- `knowledge.source-linked`
- `knowledge.sync-requested`
- `knowledge.document-synced`
- `knowledge.source-unavailable`
- `knowledge.permission-revoked`

Events reference local binding and snapshot IDs. They do not contain full document bodies or
credentials.

## Consequences

The product gains Feishu collaboration without weakening the audit model. It also retains a local
fallback and avoids coupling core Delivery startup to an external SaaS. The trade-off is a
deliberate synchronization layer and dual permission evaluation instead of pretending that an
embedded document is already trusted product knowledge.

