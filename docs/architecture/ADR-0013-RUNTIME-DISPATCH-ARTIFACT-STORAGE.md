# ADR-0013：冻结 Binding 驱动 Runtime，Artifact 使用内容寻址存储

状态：已接受
日期：2026-08-26

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
- 未知或无效 Artifact 只进入审计，不驱动交付成功。
- OpenDesign 可以作为已资格化 Skill，或作为独立 Runtime Adapter，但不能同时扮演两种身份。
