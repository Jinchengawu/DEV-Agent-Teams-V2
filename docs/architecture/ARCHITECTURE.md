---
title: Agent-Team-OS 当前架构总览
document_version: "1.0"
product_version: "0.5.0"
truth_scope: repository_revision_containing_this_file
initial_audit_baseline: 7401fa281a201728fa3cc504daa05d3a724fa7c6
last_reviewed: 2026-09-02
language: zh-CN
---

# Agent-Team-OS 当前架构总览

> 本文是后续 Agent 理解当前工程的第一入口，以及已接受架构变更的状态索引。
> 它综合解释当前 Revision 中已经存在的边界，不替代代码、Migration、OpenAPI、测试证据或 ADR。

当前代码基线已经合入 `main@7401fa281a201728fa3cc504daa05d3a724fa7c6`。这只证明代码合并；
由于真实四仓 GitHub、Hermes/Codex 与 Live Release Gate 仍为 `blocked/not_run`，v0.5.0 尚不能称为
正式 Release 验收完成。

- [打开交互架构图](../assets/architecture/agent-team-os-current.html)
- [查看静态架构图](../assets/architecture/agent-team-os-current.png)
- [查看架构决策记录](./)
- [查看完整产品文档](../product/AGENT-TEAM-OS-PRODUCT.md)

## 1. 阅读规则与事实边界

### 1.1 事实优先级

当材料发生冲突时，按以下顺序裁决：

1. 当前 Revision 的领域代码、Migration、配置锁和生成契约；
2. 绑定同一 Revision 的自动化测试、运行证据和 Release Report；
3. 已接受 ADR；
4. 本文对上述事实的综合说明；
5. 产品文档、Release Note 与 README；
6. Plan、Roadmap、讨论记录和界面文案。

本文是架构导航和当前快照，不是新的业务状态权威。发现文档与高优先级事实不一致时，应修正文档，
不能反向要求代码服从过期说明。

### 1.2 能力证据标签

| 标签 | 含义 |
|---|---|
| `Implemented` | 当前 Revision 中存在实现和契约边界，但不自动证明 Live 可用。 |
| `Deterministic Verified` | 使用固定 Adapter、Fixture 或本地 Git Remote 验证了产品状态机与证据链。 |
| `Live Blocked/Not Run` | 缺少真实 Provider、凭据、远端权限或合格 Gate Report，不能换算成通过。 |
| `Accepted/Not Implemented` | 架构审查已接受，但尚未成为当前实现。 |
| `Superseded` | 已被后续 ADR 或架构变更取代，仅保留历史可追溯性。 |

### 1.3 架构变更生命周期

```text
Proposed
→ Accepted/Not Implemented
→ Implemented/Verified
→ Superseded
```

“当前架构”章节只陈述 `Implemented/Verified` 的事实。尚未实现的内容只能进入第 12 章，不能混入
当前组件、链路或状态机。

## 2. 产品定位和非目标

Agent-Team-OS 是将多 Agent 工作组织为可验证、可审批、可恢复的软件交付的控制平面。它负责冻结
交付配置、限制运行权限、记录 Candidate/Verification/Review、实施人工 Gate，并以可审计方式 Apply。

它不是：

- 多 Agent 聊天界面或通用聊天记忆系统；
- ACWM、AgentScope、Hermes 或 Codex Runtime 的替代品；
- 将四个角色挂载到同一个 Git Workspace 的共享工作区调度器；
- 将 PR 合并按钮视为产品 Apply 权威的 GitHub Wrapper；
- 已经通过真实四仓 Live Gate 的生产交付平台。

当前软件交付模型中，Design、Frontend、Backend、QA 是四个独立 Workcell，每个 Workcell 绑定一个
独立 Git Repository Workspace。跨 Workcell 协作只传递内容寻址 Artifact、Candidate/Diff Reference
和冻结的 Provider Snapshot，不挂载其他 Workcell 的仓库。

## 3. 系统上下文

### 3.1 人与外部系统

| 参与方 | 当前职责 |
|---|---|
| Administrator | 身份、项目、Pipeline、Agent Deployment、TeamTemplate、Workspace 和系统参数治理。 |
| Delivery Operator / Editor | 发起 Delivery、处理 Gate、查看 Workcell、Evidence、Knowledge 与 Release 健康状态。 |
| Auditor / Viewer | 只读查看交付状态、不可变证据、Attempt、PR Receipt 与 Apply Receipt。 |
| ACWM | 编译并运行跨 Stage Journey、DAG、Gate、Handoff 和 bounded Loop。 |
| AgentScope | 单个产品已创建 AgentAttempt 内的 Stage-local Session、消息和 Runtime Transport 合同所有者；不得创建隐藏 Child；当前尚未接入 Workcell Live Runtime。 |
| Hermes | PM 与 Project Admin 角色智能的合同所有者；默认规划仍是 `codex-simulated-hermes`。 |
| Codex | 在隔离 Workspace 中执行受控 Role Turn 或 Workspace Write；Live 仍受凭据与 Gate 限制。 |
| GitHub / Git Remote | 提供外部 Repository 和 PR Review Surface；不拥有产品 Release Gate 或 Apply 决策。 |
| BMAD / TEA | 以冻结、只读 Method Pack Overlay 提供工作方法，不拥有 Pipeline、Workspace 或 Release。 |
| Feishu | 外部协作知识 Provider；Adapter 已有实现和测试，但 Preview 运行时尚未接线。 |

### 3.2 当前架构视图

交互图与静态图来自同一份 Archify JSON。业务连接的实线表示当前已接线链路；标注
`Provider Port · Preview 未注入` 的紫色虚线专门表示 Feishu Adapter 已存在、但默认产品运行时未接线。
虚线 Boundary Frame 只表达权威或隔离范围，不表示能力成熟度。

[![Agent-Team-OS 当前架构](../assets/architecture/agent-team-os-current.png)](../assets/architecture/agent-team-os-current.html)

## 4. 唯一权威与边界

| 领域事实 | 唯一权威 | 产品中的冻结或投影 |
|---|---|---|
| Journey、Stage、DAG、Gate、Loop、Handoff | Published Pipeline Revision / ACWM | Pipeline Revision、Graph Fingerprint、Pipeline Run Ledger |
| 单个 AgentAttempt 内的 Stage-local Session、消息和 Runtime Transport | AgentScope | 当前 Workcell Live Adapter 尚未接线；产品不复制 Runtime 内部状态 |
| PM、Project Admin 角色智能 | Hermes | 当前默认 Adapter 标识和输出 Artifact |
| 隔离工作区中的代码执行 | Codex | AgentAttempt、Workspace Access、Candidate 与日志 Artifact |
| 项目、Team、真实仓库和活动 Delivery Lease | Project Governance | Project/Team/Workspace Binding 与 DeliveryExecutionSnapshot |
| Main/Child 组合、调度、取消、超时、生命周期和结果合成 | Workcell Execution Module | WorkcellRun、AgentRun Tree、AgentAttempt、WorkcellResult |
| 权限、Verification、Approval、Evidence、Apply | Agent-Team-OS | Product Event、Evidence、Gate、Receipt、ReleaseManifest |
| BMAD/TEA 包版本、内容和入口 | Agent Deployment Extension Snapshot | Content/Qualification Hash 与 Method Binding |
| PR 页面和外部 Git refs | GitHub / Git Remote | GitHubPRReceipt、RemoteApplyReceipt；不是产品 Gate 权威 |
| 飞书协作文档正文 | Feishu | Binding、Provider Snapshot、Sync Run 和派生索引；不是 Evidence 权威 |

关键边界句：**ACWM 控制 cross-Stage delivery；产品创建并管理全部可观察 Main/Child/Attempt；
AgentScope 只承载单次 Attempt 内的 Stage-local Session、消息和 Runtime Transport；产品同时拥有业务
状态、权限、Git、Evidence、Approval 和 Apply。**

## 5. 应用结构：FastAPI 模块化单体

### 5.1 组合根

`src/agent_team_os/preview.py` 是本地产品运行时组合根：

1. 解析数据目录并执行 checksummed Migration；
2. 导入可识别的 Legacy 数据库；
3. 构建 Project Git Workspace、Repository 和领域服务；
4. 初始化 Agent Profile、Deployment、Provider Manifest 和 Runtime Extension；
5. 导入 Backend、Full-stack 和 Agent Workcell 三条内置 Pipeline；
6. 构建 Evidence、Wiki、Evaluation、Artifact、Workcell、Method Pack 与 Release 服务；
7. 通过 `create_app()` 挂载有资格的 `/v1` Router 和身份中间件；
8. 启动时恢复 provisioning Project、Delivery Lease、interrupted Attempt 和非终态 Delivery。

Preview 当前没有向 `create_app(provider_knowledge=...)` 注入 `ProviderKnowledgeManager`，因此 Feishu
Provider Router 不会进入默认产品运行时。这一点不能由存在 Adapter 类或单元测试替代。

### 5.2 产品模块

| 模块组 | Deep Module | 主要所有权 |
|---|---|---|
| 治理 | `identity`、`projects`、`agents`、`extensions`、`workcells` | 用户与权限、项目资源、Agent/Deployment、Method/Extension、Team/Workspace/Workcell |
| 编排 | `orchestration`、`delivery` | Pipeline Revision、GraphRun 投影、Delivery 状态和 Gate 协调 |
| 发布 | `releases`、`evidence`、`artifacts` | Bundle、Apply/Resume、Manifest、不可变证据和大 Artifact |
| 协作 | `knowledge`、`board` | Wiki/Provider Snapshot、Search/Publication、可重建 WorkItem 投影 |
| 运维 | `settings`、`evaluation` | 安全运行参数、Dataset/Run/Report 与 Release Gate 输入 |

模块必须通过 Application Interface、明确 Port 或已提交 Product Event 协作。Domain 文件禁止导入
FastAPI、SQLite、HTTP Client、ACWM、AgentScope 或 Uvicorn；一个模块不得直接打开另一个模块的表。
Console 按 feature slice 组织，feature 不能导入其他 feature 的实现。

### 5.3 Port / Adapter 边界

- HTTP、SQLite Repository、ACWM Gateway、Git、GitHub、Codex、Feishu 都是 Adapter。
- Domain Model 和 Application Service 保持 Runtime/Framework 无关。
- ACWM Runtime Contract 不复制到本仓库；产品只保存编译结果、绑定 Snapshot 和运行投影。
- 外部 SDK 对象不能穿过 Port 进入领域模型，必须标准化为产品 DTO 或 Artifact Reference。

## 6. 数据、持久化与一致性

### 6.1 产品状态

默认数据目录为 `.agent-team-os/`：

- `agent-team-os.sqlite` 保存产品状态、Revision、Event、Reference 和 Receipt；
- SQLite 连接启用 Foreign Key、WAL 和 busy timeout；
- Migration `0001–0035` 按校验和串行执行，已应用文件被修改时 Fail Closed；
- Command Handler 使用 UnitOfWork，使 Aggregate 状态和 Product Event 在同一事务提交；
- Board、SSE、Search 等投影只读取已提交事实，不拥有源状态。

### 6.2 大 Artifact 与 Git

- 大 Diff、日志、截图和方法包内容进入 Content-Addressed Store；SQLite 只保存 SHA-256、Media Type、
  Size 和 Reference。
- Managed Git 为每个 Project/Repository 建立独立 Bare Remote 与 Worktree。
- External Git 只绑定已存在的 HTTPS Repository，凭据保存为 `env:`/`env://` 或
  `keychain:`/`keychain://` Reference，不保存 Secret Value。
- Candidate、Diff、Verification、Review 和 Release Gate 均绑定精确 SHA；过期或不可验证 Hash
  不能驱动成功状态。

## 7. Project、Team、Pipeline 与 Snapshot

### 7.1 治理编译链

```text
TeamTemplateRevision
  + Published Pipeline Revision / ResolvedProviderBinding
  + Project Team/Workspace Binding
  + Agent Deployment Extension Snapshot
  → immutable DeliveryExecutionSnapshot
```

- TeamTemplate 只定义动态 `workcell_key`、职责、Workspace Requirement、Delegate Purpose、
  DelegationPolicy 和展示拓扑。
- Pipeline 定义 Stage 顺序、DAG、Gate、bounded Loop、Artifact Contract、Workcell Stage Map、
  Release Contract 和预解析 Slot。
- Project 绑定采用哪个 Team/Pipeline/Deployment，以及每个 Workcell 对应哪个真实 Workspace。
- Delivery 启动后只使用冻结 Snapshot，不静默解析较新的 Team、Pipeline、Provider、Workspace 或 Method。

### 7.2 Project 隔离

- Project 不是包含 Delivery、Evidence、Knowledge 和 Agent 的巨型 Aggregate；其他模块只保存
  `project_id` 或冻结 Snapshot。
- v0.5 每个 Project 同时最多一个活动 Delivery；Lease 只在终态持久化后释放。
- 当前全局角色 RBAC 已实现，但项目成员级 RBAC 尚未实现。“项目隔离”表示数据、Git 和运行作用域
  隔离，不代表完整多租户授权。

## 8. Pipeline 与 Workcell 执行

### 8.1 四仓 Journey

```text
Requirements → Tasking → Plan Gate
→ Design Repair Loop → Design Gate
→ QA Preparation（Artifact-only）
→ Frontend Repair Loop ┐
                       ├→ QA Delivery Repair Loop
→ Backend Repair Loop ┘
→ ReleaseBundleV2 Verification → Release Gate
→ External Forward-only Apply → ReleaseManifestV2
```

Design、Frontend、Backend、QA 各自只形成一条 Candidate Lineage。Frontend 与 Backend 可并行，
但不共享 Repository；QA Preparation 只产生 TestDesign/ATDD Artifact，不写 Git Candidate。

### 8.2 单个 WorkcellRun

```text
Main planning
→ DelegationPlan
→ Writer（最多一个 workspace_write）
→ Product Machine Verification
→ Reviewers（最多两个并行，只读 Candidate）
→ Main synthesis
→ Product WorkcellResult Validation
→ ACWM Stage Signal
```

固定不变量：

- 一个 ACWM Stage Attempt 对应一个 `WorkcellRun`；Repair 由 ACWM bounded Loop 创建新 Run。
- Child 深度固定为 1；Main 最多三个 Child、并发最多两个、Writer 最多一个。
- Main planning 与 synthesis 是同一 Main Run 下的不同 AgentAttempt。
- 每个 Main/Child 都必须先有产品创建的 AgentRun/AgentAttempt；Runtime Adapter 不得隐藏派生。
- Writer 使用本 Workcell 的隔离可写 Worktree；Reviewer 只读取同一 SHA 的 detached Candidate。
- Writer 的机器验证失败时 Reviewer 不启动；Blocking Review 或失败 Verification 不能被 Main 覆盖。
- Child 之间只传递内容寻址 ArtifactEnvelope，不传原始 Session、Memory 或聊天历史。
- Cancel 向未完成 Child 传播并终止 Codex 进程；重启时不可恢复 Attempt 标记为 `interrupted`。

## 9. Release V1/V2 与故障恢复

### 9.1 External Forward-only V2

1. 每仓 Candidate Branch 为 `agent-team-os/{delivery_id}/{workcell_key}`，禁止 Force Push；
2. GitHub PR 绑定 Base、Head 和 Candidate SHA，但只是 Review Surface；
3. Release Gate 绑定不可变 `ReleaseBundleV2.bundle_sha256`；
4. Apply 前 Fetch 并要求远端 `main == reviewed base`；
5. 顺序执行非 Force Fast-forward Push，随后回读远端 SHA；
6. 四仓全部回读等于 Candidate 后才激活 `ReleaseManifestV2`。

部分 Apply 失败时：

- 已成功仓库不回滚、不 Force Push；
- Delivery 进入非终态 `needs_attention`；
- Project 进入 `release_drifted`，Lease 继续持有；
- `resume-forward` 只接受原 Bundle，并验证已应用仓仍为 Candidate、未应用仓仍为 Base；
- 条件不满足时继续人工协调，不自动 Rebase、改写 Bundle 或生成补偿提交。

### 9.2 Managed Git V1

历史 Managed Bare Git、`RepositoryCandidate`、`ReleaseBundleV1`、CAS Compensation 和旧 Manifest
继续保留原语义。ADR-0015 只替代外部 Git Apply 策略，不原地泛化 V1 历史模型。

## 10. 状态、权限与安全边界

### 10.1 关键状态

| 对象 | 关键状态与终态规则 |
|---|---|
| Project | `provisioning → active | provision_failed`，`active → archived`；当前不恢复 archived。 |
| Delivery | `queued`、`planning`、人工 Gate、`executing`、`verifying`、`applying`、`needs_attention`；终态为 `completed/rejected/failed/cancelled`。 |
| WorkcellRun | `planning → delegating → verifying → reviewing → synthesizing`；终态为 `succeeded/failed/cancelled/timed_out/interrupted`。 |
| AgentAttempt | `running` 后进入 `succeeded/failed/cancelled/timed_out/interrupted`，非可恢复进程不伪装续跑。 |
| Release Health | `healthy | release_drifted`；只有全部远端回读一致才能恢复 healthy。 |

`needs_attention` 不是 Delivery 终态，因此不能释放 Project Lease，也不能激活不完整 Manifest。

### 10.2 身份与信任

- 本地 Identity 使用 Session Cookie、CSRF、同源校验和角色 Permission；缺少身份时 API Fail Closed。
- 当前权限角色是全局 Administrator、Editor、Viewer，不等同项目级成员 RBAC。
- Credential 只允许 Reference；Secret 不得进入 Git、SQLite、API、日志、Evidence、截图或文档。
- Reviewer 的只读语义由 detached Candidate View 和 `candidate_read` Workspace Access 表达，
  不使用 Codex `--add-dir` 模拟权限。
- PR 状态、UI “Ready” 标签、Agent 自报能力和未验证 Hash 都不能成为 Apply 依据。

## 11. Knowledge、Evidence 与当前成熟度

### 11.1 Knowledge 与 Feishu

- Local Wiki、Revision、Publication、FTS Search、Role Document 和 Knowledge Derivation 已接入 Preview。
- Provider Binding、Snapshot、Sync Run、Feishu Adapter 和失败映射已有实现及 Contract Test。
- Preview 未构造 `ProviderKnowledgeManager`，也未注入 Feishu 用户授权解析，因此默认产品 API/Console
  没有形成可操作的 Feishu 闭环。
- Feishu 是显式链接的协作知识正文权威；产品仍拥有 Binding、Snapshot、Provenance、SHA、Source Policy
  和派生 Search Index。
- Delivery Evidence 始终由 Agent-Team-OS 拥有；Feishu 内容不能覆盖 Evidence。

### 11.2 能力成熟度

| 能力 | 实现状态 | 证据边界 |
|---|---|---|
| FastAPI 模块化单体、SQLite/UoW/Event | `Implemented` | Architecture/Repository/Migration 测试；不是生产 SLA。 |
| Pipeline Catalog、ACWM GraphRun、Gate/Loop | `Implemented`、`Deterministic Verified` | 本地 Pipeline 与浏览器门禁；真实 Runtime 仍需 Live Gate。 |
| Project/Team/Workspace/Delivery Snapshot | `Implemented`、`Deterministic Verified` | 本地多 Project 和四 Bare Remote。 |
| Workcell Main/Child/Attempt Kernel | `Implemented`、`Deterministic Verified` | 调度、验证、Review、取消和恢复；不证明模型质量。 |
| BMAD/TEA Content-Addressed Overlay | `Implemented`、`Deterministic Verified` | 包完整性和无业务仓库污染；不证明方法效果。 |
| External Git、GitHub PR、Forward-only V2 | `Implemented`、`Deterministic Verified`、`Live Blocked/Not Run` | 真实四个私有 GitHub 仓库与直推身份尚无合格 Report。 |
| Hermes Live | `Live Blocked/Not Run` | 默认规划仍为 `codex-simulated-hermes`，不得冒充真实 Hermes。 |
| AgentScope Attempt Runtime | Manifest/合同 `Implemented`；Workcell Live Adapter `Accepted/Not Implemented` | 当前 Main/Child 由产品直接调度 Codex Attempt，不是 AgentScope Live 证据。 |
| Local Knowledge | `Implemented` | Wiki/Search/Publication 已接线。 |
| Feishu Knowledge | Adapter `Implemented`；产品闭环 `Accepted/Not Implemented` | 默认组合根未接线；本次不实施。 |
| Evaluation | API/CLI/Dataset `Implemented` | 当前没有独立 `/evaluation` Console 页面。 |

已知后移能力包括 Workspace-Set 跨 Delivery Lease、Delta Release、Manifest Version CAS、
并行 Manifest 合成、非 Git Workspace Adapter、二级子 Agent 和 Provider-native PR Merge。

## 12. Accepted Architecture Changes

### 12.1 `Accepted/Not Implemented`

### 12.1.1 `ARCH-20260902-02` AgentScope Attempt Runtime 边界

```text
State: Accepted/Not Implemented
Accepted at: 2026-09-02
Architecture Impact: Cross-boundary
Decision: Workcell Execution 拥有全部可观察 Main/Child 组合与生命周期；AgentScope 只拥有产品已创建
          AgentAttempt 内的 Stage-local Session、消息和 Runtime Transport，禁止隐藏派生。
Affected authorities/modules/data/states: Workcell Execution、AgentScope Adapter、AgentRun、AgentAttempt
Compatibility and migration: 不改变现有 AgentRun/Attempt 数据；当前 Codex 直连 Adapter 继续保留，
                             直到 AgentScope Attempt Runtime 通过资格与 Live Gate。
Plan/ADR reference: ADR-0014（2026-09-02 修订）
Acceptance evidence required: Adapter 合同、身份绑定、取消/超时传播、无隐藏 Child、重启中断和 Live Gate
```

Feishu 产品接线沿用 ADR-0008 的边界，尚未进入本次实施范围。

新条目必须使用以下结构：

```text
ARCH-YYYYMMDD-NN
State: Accepted/Not Implemented
Accepted at:
Architecture Impact:
Decision:
Affected authorities/modules/data/states:
Compatibility and migration:
Plan/ADR reference:
Acceptance evidence required:
```

## 13. 架构变更台账

| Change ID | 日期 | 状态 | 变更 | ADR | 验证证据 |
|---|---|---|---|---|---|
| `ARCH-20260902-01` | 2026-09-02 | `Implemented/Verified` | 建立架构事实总览、单一可视化模型和 Plan Architecture Review 门禁 | 不需要；未改变运行时权威 | 文档结构测试、Ruff、Archify showcase 9/9、四档 containment 与双主题人工视觉复核通过 |
| `ARCH-20260902-02` | 2026-09-02 | `Accepted/Not Implemented` | 产品拥有可观察 Workcell Composition；AgentScope 仅拥有单次 Attempt Runtime，禁止隐藏 Child | ADR-0014 修订 | 待完成 AgentScope Adapter 合同、取消/中断和 Live Gate |

## 14. Plan Architecture Review 与文档对账

涉及仓库变更的 Plan 必须按以下顺序完成：

```text
Draft Plan
→ Architecture Review
→ Revise Plan
→ Final Plan
→ Implementation
→ Architecture Reconciliation
```

最终 Plan 必须包含 `Architecture Impact`、`Findings`、`Required Revisions`、`ADR Required`、
`Architecture Document Delta` 和 `Outcome`。只有改变权威、模块依赖、持久化/恢复、安全、Release
语义或外部集成策略时才需要 ADR。

实施完成后：

- 无架构影响：保留 Review 结论，不修改本文；
- 有架构影响但尚未实现：写入第 12 章；
- 已实现并验证：修改相应当前架构章节，并在台账中晋升为 `Implemented/Verified`；
- 被替代：保留历史条目并标记 `Superseded`，链接取代它的 ADR 或 Change ID。

## 15. 后续 Agent 最小理解检查

阅读 `AGENTS.md` 与本文后，Agent 应能回答：

1. 产品为什么不是多 Agent 聊天系统；
2. ACWM、AgentScope、Hermes、Codex 和 Agent-Team-OS 各拥有什么权威；
3. Design、Frontend、Backend、QA 为什么不共享 Git Workspace；
4. Workcell Main、Child、AgentAttempt、Verification 和 Review 如何串联；
5. Candidate 何时能 Apply，Partial Apply 如何恢复；
6. Deterministic 与 Live 证据有何区别；
7. 哪些能力已经接线，哪些只是 Adapter、ADR 或 `Accepted/Not Implemented`；
8. 新 Plan 如何进行 Architecture Review，并在实施后与本文对账。
