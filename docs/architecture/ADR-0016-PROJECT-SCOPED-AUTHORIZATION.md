# ADR-0016：Project-scoped Authorization

状态：已接受；Project RBAC 切片已实现并完成 `Deterministic Verified`，复合变更
`ARCH-20260902-03` 仍为 `Accepted/Not Implemented`

日期：2026-09-02

架构变更：`ARCH-20260902-03`

Architecture Impact：`Critical`

## 实现对账（2026-09-02）

- Migration `0036` 与 `0040` 已加入 `ProjectMembership`、Project/Identity Authorization Version
  及审计所需持久化结构；未修改历史 Migration。
- Project Access Policy、最后 Owner 保护、Administrator bypass Receipt、Approved Source Scope 与
  Project-scoped HTTP 授权已落地，并由公共接口与跨项目直接 ID 测试验证。
- 这些证据只证明当前本地 Revision 的授权状态机；不证明生产多租户隔离、真实 Feishu 权限或 Live SLA。
- 本 ADR 所属复合变更仍需 ACWM Gate C、真实 Tenant/Ollama 与同 Revision Release Gate，不能单独晋升
  `ARCH-20260902-03`。

## 背景

ADR-0006 定义全局 Administrator、Editor、Viewer；ADR-0011 定义 Project 数据、Git、Lease 和
查询作用域，但明确没有项目成员级 RBAC。飞书知识来源会把外部协作内容批准给具体 Project，
仅依赖全局角色会导致用户能够越过项目成员边界读取 Knowledge、Delivery、Evidence 或 Release。

只在 Knowledge Router 增加局部检查也不足以形成 Project RBAC：直接 ID 查询、Activity、
Derivation、Artifact、Board、Workcell 和 Release 都可能形成旁路。

## 决策

新增 `ProjectMembership`，其 `ProjectRole` 为 `owner | editor | viewer`。有效授权统一为：

```text
Effective Permission
= Global Role Capability
∩ ProjectRole Capability
∩ Resource Policy
∩ Approved Source Scope（仅知识资源）
```

Global Role 是能力上限；ProjectRole 只会缩小，不会扩大全局权限。统一 Project Access Policy
必须保护所有 Project-scoped Application Interface，包括 Project、Delivery、Board、Evidence、
Knowledge、WorkcellRun、AgentAttempt、Artifact 和 Release。Console 可见性不是授权边界。

`ProjectScopedResourcePolicy` 维护 Canonical Resource Matrix，至少覆盖 List、Direct ID、
Command、SSE、Export、Artifact Download、Activity、Derivation、Snapshot、RetrievalRun 和
正文 Inspect。OpenAPI/公共接口测试必须从该矩阵检查覆盖，模块不得维护互相漂移的资源清单。

Administrator 可以旁路 Membership 处理治理和恢复，但：

- 仍受资源状态、Approved Source Scope 和不可变 Evidence 规则约束；
- 每次旁路产生 actor、project、resource、action、reason 和时间戳审计事件；
- 不能使用旁路伪造历史 Membership。

## 不变量

1. 新 Project 的创建者成为首个 Owner。
2. 除 `legacy-default` 等显式 Legacy 例外外，活动 Project 必须始终保留至少一个有效 Owner；删除、
   禁用或降级最后 Owner 时 Fail Closed。
3. Membership 修改使用 `expected_version` CAS。
4. disabled User 不具有 Project Capability；历史审计身份继续可读。
5. archived Project 沿用 ADR-0011 的只读边界，不能新增成员、扩大 Scope、同步或发起 Delivery。
6. Project A 的直接资源 ID 不能绕过 Project B 的 Membership。
7. Query Key、SSE、Board Projection、导出与 Artifact 下载都必须携带或解析同一 Project Scope。
8. `legacy-default` 不生成虚假 Owner，由 Administrator 旁路治理。

Identity Module 禁用 User 时不能绕过第 2 条。跨模块 Application Coordinator 必须通过只读
`ProjectOwnershipGuard` 检查该 User 是否为任一活动 Project 的最后有效 Owner；命中时拒绝禁用并要求
先完成 Owner 移交。Identity 不直接查询 Project 表，Project Governance 也不修改 User 状态。

## 角色能力基线

| 能力 | owner | editor | viewer |
|---|---:|---:|---:|
| 查看项目资源 | 是 | 是 | 是 |
| 发起 Delivery、检索、请求同步 | 是 | 是 | 否 |
| 处理允许的 Delivery Gate | 是 | 是 | 否 |
| 管理 Project Membership | 是 | 否 | 否 |
| 启停已批准的 Project Source | 是 | 否 | 否 |
| 扩大管理员批准的外部 Scope | 否 | 否 | 否 |

精确 Permission Identifier 由实现切片在 OpenAPI 与权限矩阵中冻结，不允许不同模块自行复制角色判断。

## Migration 与兼容

- Migration 从 `0036_project_memberships.sql` 开始，不修改 `0001–0035`；
- 只有仍能解析到合法 User 的 `created_by` 才能显式投影为 Owner；
- 缺失、`system` 或已删除身份不生成虚假 Membership；
- 当前 Revision 在迁移完成前仍使用 ADR-0006 全局权限，本文不得作为已实现证据。

## 验证

- 全部 Project-scoped HTTP 接口使用公共授权矩阵测试；
- 覆盖列表、直接 ID、Activity、Derivation、Snapshot、Artifact 和 Release 旁路；
- 覆盖 Membership 修改和 Identity 禁用两条最后 Owner 路径、CAS 冲突、disabled User、
  archived Project 和 Administrator bypass 审计；
- 浏览器测试两个 Project、三个角色、直接 URL、刷新缓存和 Query Key 隔离。

## 结果

项目数据隔离将升级为统一授权边界，同时保留 ADR-0006 的本地身份安全和 ADR-0011 的 Project
数据所有权。代价是所有项目级模块必须依赖统一 Policy Port，而不是各自解释 Role。
