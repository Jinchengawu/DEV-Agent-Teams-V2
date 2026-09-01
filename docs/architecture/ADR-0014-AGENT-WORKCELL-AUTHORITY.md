# ADR-0014：Agent Workcell 权威关系与隔离工作区

状态：已接受
日期：2026-08-31
修订：2026-09-02

## 背景

设计、前端、后端和 QA 是四个可独立交付的软件工作单元。它们拥有各自的
Git Repository，不是同一 Git Workspace 中的四个 Agent 角色。仅用聊天历史或
Agent 内部派生来协作，会丢失调度、取消、超时、工作区权限、验证和评审证据。

## 决策

Agent-Team-OS 引入 `Agent Workcell` 作为 Stage 内的产品级执行单元，并按下表划分
唯一权威：

| 领域事实 | 唯一权威 |
|---|---|
| Stage、DAG、Gate、Loop、Artifact Contract、Provider Binding | Published Pipeline Revision / ACWM |
| Workcell 身份、拓扑、Workspace 要求、Delegation 上限 | TeamTemplate Revision |
| Team 选择和真实仓库绑定 | Project Governance |
| Main/Child 组合、调度、取消、超时、生命周期和结果合成 | Workcell Execution Module |
| 单个 AgentAttempt 内的 Stage-local Session、消息和 Runtime Transport | AgentScope |
| Candidate 验证、Approval、Apply、Manifest | Release Module |
| BMAD/TEA 版本、内容和入口 | Agent Deployment Extension Snapshot |

`TeamTemplateRevision` 不包含 Stage 顺序、Provider、Deployment、Runtime Instance、凭据、
真实仓库或 Release Participant。Pipeline 发布时冻结 `workcell_stage_map`、
`release_contract_snapshot` 和所有 `main`/`delegate_*` 的 `ResolvedProviderBinding`。Delivery
启动时将 Team、Pipeline、Provider、Workspace 和 Method Pack 编译为不可变
`DeliveryExecutionSnapshot`。

## 执行不变量

1. 一个 ACWM Stage Attempt 对应一个 `WorkcellRun`。
2. `AgentRun` 表示逻辑 Main/Child；`AgentAttempt` 表示一次真实 Provider 调用。
3. Main planning 和 synthesis 是同一 Main Run 下的两个 Attempt。
4. Child 深度固定为 1；每个 Main 最多三个 Child，最多两个并发，最多一个
   `workspace_write` Child。
5. Writer 使用本 Workcell 的隔离可写 Worktree；Reviewer 只读同仓已冻结 Candidate。
6. 不挂载其他 Workcell Repository，也不用 Codex `--add-dir` 伪装只读跨仓输入。
7. 跨 Workcell 仅传递已校验的内容寻址 `ArtifactEnvelope`；不传 Session、Memory 或原始聊天历史。
8. Writer 的机器验证通过后 Reviewer 才能启动；Main 不能覆盖机器失败或
   Blocking Review。
9. Repair 由 ACWM bounded Loop 创建新 `WorkcellRun`，不在 Child 中递归派生。
10. 取消 Workcell 会取消其 Delivery 执行任务，传播到未完成 Child，并终止正在运行的
    Codex 子进程。重启时未完成 Codex Attempt 标记为 `interrupted`，不伪装续跑成功。
11. 每个 Main/Child 必须先由产品创建 `AgentRun` 与 `AgentAttempt`，Runtime Adapter 才能执行；
    AgentScope 不得产生 `Hidden Child`（隐藏派生），即在产品不可见的位置派生 Child 或新的运行身份。

## AgentScope Attempt Runtime 边界

2026-09-02 的架构审查明确：可观察的 Workcell Composition 与生命周期属于 Workcell Execution
Module；AgentScope 只拥有一个产品已创建 `AgentAttempt` 内的 Stage-local Session、消息和 Runtime
Transport。这样既保留 AgentScope 的运行时能力，又避免其内部 Team/Child 绕过产品的并发、权限、
取消、证据和审计约束。

当前 Preview 仍由产品直接调度 Codex Main/Child Attempt；真正的 AgentScope Attempt Runtime Adapter
尚未接线，按 `Accepted/Not Implemented` 管理。在接线前，不得把 Workflow Manifest 或依赖包存在
表述为 AgentScope Live 证据。

## Method Pack Overlay

BMAD/TEA 是方法扩展，不是新的 Pipeline 或 Git 权威。产品只从锁定的 npm 归档构建
内容寻址、只读、临时 `CODEX_HOME` Overlay；不执行安装脚本，不将 `_bmad`、
`.agents/skills` 或其他安装产物写入业务仓库。Party Mode 不在允许入口中。

## 结果

- 四个角色的 Git 边界与 Agent 组织边界一致。
- Main/Child/Attempt 都成为产品可见实体，可以审计取消、超时、绑定和产物。
- AgentScope 拥有单次 Attempt 内的 Session、消息与 Runtime Transport；ACWM 仍然拥有跨 Stage
  工作流；产品拥有可观察 Workcell Composition，但不复制两者的 Runtime Contract。
- 旧 Delivery、`RepositoryCandidate`、`ReleaseBundleV1` 和历史 Snapshot 保持可读。
