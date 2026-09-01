# DEV-Agent-Teams V2 工程规则

- 产品定位：Agent-Team-OS 是交付控制面，不是多 Agent 聊天界面。
- ACWM 保持为跨 Stage 控制面；不得在本仓库复制其 Runtime Contract。
- Agent-Team-OS Workcell Execution 拥有产品可观察的 Workcell Composition（Main/Child 组合）、
  调度和生命周期。
- AgentScope 只拥有产品已创建 `AgentAttempt` 内的 Stage-local Session、消息与 Runtime Transport；
  不得创建产品不可见的 `Hidden Child`。
- Hermes Instance 拥有 PM 和 Project Admin 角色智能。
- Codex 拥有隔离工作区中的受控代码执行。
- 产品代码拥有权限、候选验证、Verification、Approval 和 Apply 策略。
- 以竖向交付切片建设功能，并使用公共接口测试验证。
- 不得将 Deterministic Adapter 表述为 Live Agent 证据。
- 未经明确决策，不得从旧 DEV-Agent-Teams 仓库迁移代码。
- 每个主要产品版本必须在同一 Git Revision 上通过核心用户闭环的浏览器冒烟测试，以及
  Deterministic 与 Live Release Gate。Release Report 必须满足 `FAIL=0`、`WARN=0` 和
  `skipped=0`，否则不得视为已验收。
- 每次主要版本交接必须提供独立的本地评测账号；密码不得进入 Git、应用日志、Fixture、
  Gate Report、截图或已提交文档。

## 架构认知与 Plan 门禁

- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) 是后续 Agent 理解
  当前工程的第一入口和已接受架构变更的状态索引。它不替代当前 Revision 的代码、Migration、
  OpenAPI、测试证据或 ADR。
- 每个涉及仓库变更的 Plan 必须遵循：
  `Draft Plan → Architecture Review → Revise Plan → Final Plan → Implementation → Architecture Reconciliation`。
- Architecture Review 必须在最终 Plan 之前完成，并显式输出：
  `Architecture Impact`、`Findings`、`Required Revisions`、`ADR Required`、
  `Architecture Document Delta` 和 `Outcome`。
- `Architecture Impact` 只允许 `None`、`Local`、`Cross-boundary` 或 `Critical`；
  `Outcome` 只允许 `Approved`、`Revise` 或 `Blocked`。实质修订后的 Plan 必须重新检查受影响的
  架构边界。
- Review 至少检查产品定位与权威归属、Module/Port/Adapter 依赖方向、数据所有权、状态机、
  并发与恢复、权限与 Workspace 隔离、Legacy/Migration、可观测性，以及 Deterministic/Live
  证据边界。不得用重复事实源或非必要复杂度掩盖问题。
- 只有改变系统权威或模块所有权、跨模块依赖方向、持久化/一致性/恢复模型、安全信任边界、
  Release/Apply/补偿语义、外部系统集成策略，或取代既有 ADR 时，才要求新增或修订 ADR。
  局部 UI、普通 API 字段和不改变边界的内部重构不强制创建 ADR。
- Review 通过且存在架构变化时，必须在实施变更集中同步更新架构总览：尚未实现的内容只能进入
  `Accepted/Not Implemented`，完成实现与验证后才能晋升为 `Implemented/Verified`。
  Plan Mode 不允许写文件时，最终 Plan 必须给出完整待写入条目，并在下一次获准写入时持久化。
- `Architecture Impact: None` 时仍要保留 Review 结论，但不得为了流程而制造架构文档噪声。

## 语言与文档规范

- 文档产出中文优先。仓库文档、代码注释、CLI/API 描述和功能介绍默认使用简体中文。
- Canonical Identifier、API 名、错误码、命令及必要技术术语保留英文，不做会破坏契约的强制翻译。
- 需要英文文档时，中文文档保留在默认路径，英文版使用明确的 `.en.md` 伴随文件，不得替换中文默认文档。
