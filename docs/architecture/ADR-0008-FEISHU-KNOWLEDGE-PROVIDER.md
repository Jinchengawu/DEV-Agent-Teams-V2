# ADR-0008: Feishu Is a Collaborative Knowledge Provider, Not the Evidence Authority

Status: accepted for a V0.2.1 proof of concept after the local Wiki golden slice. The user-auth
and embedded-editor clauses are superseded for the v0.5.1 target by ADR-0017. Tenant Gate A is now
implemented behind a Feature Flag and Deterministic verified; the composite target remains
`Accepted/Not Implemented` and has no Live proof.

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

## 2026-09-02 v0.5.1 目标关系

以下决定继续有效：

- Feishu 拥有显式绑定的人工知识正文；
- Agent-Team-OS 拥有 Binding、不可变 Snapshot、Provenance、Hash、Source Policy 和派生索引；
- Delivery Evidence 只属于 Agent-Team-OS，Feishu 内容不能覆盖 Evidence；
- Local Wiki 保持离线/default Provider，Feishu 不能成为既有产品启动依赖。

ADR-0017 为可信本机 Alpha 接受 Tenant App Service Principal，并取代本 ADR 面向目标版本的
逐用户 User Token、Feishu ACL 交集、Docs Component、Embed Grant 和 Webhook 要求。项目访问改由
`Global Role ∩ ProjectRole ∩ Resource Policy ∩ Approved Source Scope` 决定。
目标模型中，Knowledge Module 继续拥有 Provider Binding、Source Head 与 Snapshot Policy；
Project Governance 单独拥有 `ProjectKnowledgeSourceApprovalV1`，二者通过 Policy Port 关联，
不形成两个 Source Approval 权威。

当前 Revision 已新增 Tenant App Adapter、Migration 与 Gate A 组合根接线；现有
`ProviderActor`、`ProviderActorResolver` 和 `resolve_user_access_token` 仍作为独立 Legacy 路径保留，
没有被原地伪装迁移。真实 Tenant 凭据的 Live PoC 尚未运行，Deterministic Provider 不能替代它。
