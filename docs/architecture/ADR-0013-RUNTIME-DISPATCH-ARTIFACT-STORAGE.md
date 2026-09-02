# ADR-0013：冻结 Binding 驱动 Runtime，Artifact 使用内容寻址存储

状态：已接受；Codex 与 Hermes ACP 产品 Runtime Adapter 已实现并完成 Deterministic Contract 验证，
真实 Hermes Live 仍为 `blocked/not_run`
日期：2026-08-26

最近实现对账：2026-09-02

## 决策

Published Pipeline Revision 中冻结的 Resolved Provider Binding 必须决定实际 Runtime
Adapter、Agent Profile 指令、Permission、Repository Role 和 Extension Snapshot。AgentRun
不得只记录身份后继续调用全局 Planning 或 Code Executor。

ACWM 继续拥有 Capability、Workflow、Provider、Artifact Contract 和兼容性语义。
Agent-Team-OS 拥有 Runtime Dispatch、Artifact 内容存储、业务投影、Evidence、Git 和最终
副作用。

大型 Artifact 使用内容寻址存储。SQLite 只保存 Artifact Reference、Media Type、Size 和
SHA-256，不保存截图、设计资源、长日志或大型 Diff 正文。产品只管理 Skill、Plugin 和 MCP
的安装与资格事实；执行语义仍来自 Runtime Adapter 与 ACWM Provider Manifest。

## 结果

- Codex Role Turn 与 Codex Workspace Write 是两个真实 Adapter。
- Hermes ACP Role Turn 通过 Published Pipeline 中冻结的 Deployment、Runtime Instance Version、
  Runtime Identity、`ResolvedCapability` 与连接配置指纹选择；不允许在执行时静默重解析 Provider。
- Hermes 规划 Attempt 使用逐 Attempt 的临时空目录，目录权限为 `0700`，结束后删除；Read/Search/Fetch、
  Workspace Edit 和未列入 Allowlist 的 Command 均 Fail Closed。产品只接纳通过
  `RequirementArtifact` / `TaskContract` Schema、Acceptance ID 与冻结 Citation 集校验的输出。
- `hermes.acp` 已接入产品 Dispatcher；`http.sync` 尚未接入。Live Readiness 必须逐个核对 Published
  Planning Slot 实际冻结的 Adapter，并执行 `hermes acp --check`，不能因任意 Hermes CLI/Adapter
  已安装而放行。
- 未知或无效 Artifact 只进入审计，不驱动交付成功。
- OpenDesign 可以作为已资格化 Skill，或作为独立 Runtime Adapter，但不能同时扮演两种身份。

上述实现只证明产品 Runtime 合同和失败关闭边界。默认内置 Planning Slot 仍使用
`codex-simulated-hermes`；缺少真实凭据、已资格化 Published Binding 和同 Revision Live Gate 时，
不得声称 Hermes 已参与交付。
