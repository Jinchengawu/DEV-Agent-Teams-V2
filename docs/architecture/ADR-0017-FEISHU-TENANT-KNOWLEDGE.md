# ADR-0017：Feishu Tenant Service Principal 与可靠同步

状态：已接受；Tenant Knowledge Gate A 已实现并完成 `Deterministic Verified`，复合变更
`ARCH-20260902-03` 仍为 `Accepted/Not Implemented`

日期：2026-09-02

架构变更：`ARCH-20260902-03`

Architecture Impact：`Critical`

## 实现对账（2026-09-02）

- Migration `0037`、`0038` 已加入 Tenant Connection、Binding、Source Head、不可变 Snapshot 与
  可租赁 Sync Job；Preview 在 Gate A Feature Flag 启用时注入 `TenantKnowledgeManager`。
- Tenant 身份诊断、Space 发现、Scope 冻结、Source 级权限新鲜度、重试/恢复、Source 隔离和内容寻址
  Snapshot 已通过 Deterministic 契约及浏览器闭环验证。
- `knowledge-sync-runtime-v1` 已在 Preview 与 Deterministic Gate App 生命周期接线：Scheduler 以稳定
  15 分钟桶只创建持久化 Job，Worker 通过五分钟 Lease 自动领取到期重试，单进程并发固定为 2、
  最多尝试 5 次；完整目录最迟每 24 小时对账。进程启动只回收过期 Lease，不以内存任务作为权威。
- 旧 `ProviderActor` / User Access Token Adapter 继续保留为 Legacy 路径，不参与 Tenant App RAG。
- 尚未使用真实企业自建应用凭据执行 Live PoC；Deterministic Provider 不能作为真实飞书证据。

## 背景

当前 Feishu Adapter 使用 `ProviderActor` 和 User Access Token，ADR-0008 还要求逐用户 Feishu
访问交集与 Docs Component。v0.5.1 面向可信本机 Alpha，需要后台同步和无人值守 Delivery；
逐用户 OAuth 会让同步、Token 刷新和 Agent Acting Identity 成为新的运行时依赖。

## 决策

`FeishuAccessModel` 采用 `tenant-service-principal-v1`：

- 企业自建应用使用 Tenant App 身份访问 Administrator 明确批准的 Wiki Space/子树；
- 不引入用户 OAuth、个人 Feishu ACL 交集、Docs Component 或 Embed Grant；
- 项目成员按 ADR-0016 的产品权限共享 Approved Source；
- UI 必须提示产品可见范围可能不同于个人 Feishu 可见范围；
- Secret 只保存 `env:`/`keychain:` Reference，不进入数据库、日志、API、Evidence 或文档。

模型拆分为：

- `FeishuTenantAppConnection`：Tenant 身份、Secret Reference、资格、状态和
  Connection Authorization Version；
- `KnowledgeProviderBinding`：一个 Connection 到一个已验证 Wiki Space；
- `ProjectKnowledgeSourceApprovalV1`：Project Governance 拥有的 `space | subtree` 批准记录，
  包含 Binding、根 Node、后代策略、允许类型和 SHA；
- `ProviderSourceHead`：当前 Revision、Snapshot、路径与可用状态；
- 不可变 `ProviderSnapshot`：规范化 Docx Block、Anchor、Revision、URL 与 SHA-256。

Feishu 继续拥有人工正文；Knowledge Module 拥有 Connection、Binding、Source Head、Snapshot、
同步、索引和检索；Project Governance 拥有 Membership 和 Project Knowledge Source Approval，
只通过 Policy Port 关联 Binding，不复制 Snapshot 或 Index。Evidence 权威仍按 ADR-0008 保留在产品。

Connection Authorization Version 只在 Tenant 身份、资格、启停或其他权限性状态变化时单调递增；
普通 Sync 进度、诊断时间戳和文档内容 Revision 不得改变该版本，避免无关内容变化误撤销已经冻结的
Delivery。内容变化由 Provider Revision、Snapshot 和 Index Revision 表达。

## 同步可靠性

`KnowledgeSyncJob` 是持久化、可租赁任务：

```text
queued → leased/running → retry_wait → succeeded | failed | cancelled
```

- Scheduler 只入队，Job Repository 是状态权威；
- Worker 使用带过期时间的数据库 Lease，进程重启后可回收；
- Binding/Source/Revision 抓取去重，Project 只请求其 Approved Scope；
- `401` 先刷新 Tenant Token，持续认证失败才降级 Connection；
- 单 Source `403/404` 只 quarantine/tombstone Source；
- `429/5xx` 有上限退避、抖动并遵守 `Retry-After`；
- 删除、移出 Scope 或失权的 Source 不进入新 Search/RAG，历史 Snapshot 仅供授权审计；
- `KnowledgeFreshnessPolicyRevision` 的 v1 参考值为
  `max_permission_probe_age=30m`；成功抓取更新 Source Head 的探测时间，超过最后成功 Source 探测时间时
  Source 必须 Fail Closed，
  禁止新 Retrieval/Attempt，而不是无限期使用旧权限结果；
- 首版只支持单受监管 Worker，不宣称多实例调度安全。

飞书侧撤权只有在同步或权限探测后才能发现。产品必须展示最后探测时间，不承诺即时远端撤权；
`max_permission_probe_age` 是新使用的安全上限，不是对已经发送给模型内容的召回承诺。

## 信任边界

所有 Feishu 内容属于 `KnowledgeTrustClass=external-collaborative`。Snapshot 和 SHA 只证明来源与
完整性，不赋予指令权威。Agent 不得因文档正文调用工具、扩大 Scope、访问其他 Workspace 或改变
System/Developer Instruction。

## 与 ADR-0008 的关系

本 ADR 保留 ADR-0008 的内容权威、不可变 Snapshot、本地 Wiki、Evidence 隔离和 Provider-neutral
Port；取代目标版本中的 User Token、个人 ACL 交集、Docs Component、Embed Grant 和 Webhook
要求。现有代码在新 Port/Adapter 和 Migration 完成前继续作为当前事实，不原地伪装迁移。

## 验证

- 最小只读权限的真实 Tenant Token、Wiki Space、Node 和 Docx Blocks PoC；
- Secret 泄漏、Scope 扩大、跨 Project、Revision 冲突和 Snapshot 幂等测试；
- Job Lease、重启、重复请求、`401/403/404/429/5xx` 与 Source 隔离测试；
- 权限探测超过 `max_permission_probe_age` 后，新 Retrieval/Attempt Fail Closed；
- 无 Feishu 配置时 Local Wiki、Delivery、Evidence 和 R1 Pipeline 不退化；
- Mock/Deterministic 结果不得标记为 Live。

## 结果

系统获得适合受控 Alpha 的后台知识同步，但明确接受“按项目共享而非按个人飞书 ACL”的安全取舍。
生产多租户或逐用户权限需要新的 ADR，不得从本决策隐式推导。
