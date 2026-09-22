# Software Delivery Engineer Agent 落地计划

日期：2026-09-20
状态：Locally Verified

## 目标与非目标

目标是在仓库内提供一个 Codex 项目级自定义 Agent 和同名 Skill，用于功能开发、缺陷修复、重构、测试、CI 修复与实现审查，并强制沿用本仓已有的架构门禁和交付证据边界。

本变更不引入新的 Agent Runtime、Pipeline、Stage、Workcell、数据库表、Provider、MCP、生产权限或发布自动化；不替代 BMAD/TEA、ACWM、AgentScope 或 Agent-Team-OS 控制面。

## Draft Plan

1. 增加 `.codex/agents/software-delivery-engineer.toml`，定义窄职责项目 Agent。
2. 增加 `.agents/skills/software-delivery-engineer/`，沉淀实现、测试、审查和证据报告流程。
3. 将仓库 CI 对齐命令放入按需读取的 reference，避免主 Skill 过长。
4. 校验 TOML、YAML、Skill 元数据和 Git diff；不触发 Commit、Push、PR、Release 或 Apply。

## Architecture Review

- `Architecture Impact`: `Local`
- `Findings`:
  - 变更只作用于 Codex 项目级协作配置，不进入产品 Runtime 或 Candidate。
  - Agent 必须服从现有唯一权威：ACWM 管跨 Stage 语义；Agent-Team-OS 管可观察 Workcell 与 Release/Apply；AgentScope 不得产生 Hidden Child；BMAD/TEA 仅为 Method Overlay。
  - 仓库当前已有项目级 Product Manager Agent/Skill，本 Agent 应与其形成“产品定义/验收”和“受控实现/验证”的职责分离。
  - 自动 Commit、Push、PR、Merge、Gate Approval、Apply 或部署会扩大权限，不应成为默认行为。
- `Required Revisions`:
  - 自定义 Agent 不固定模型，继承调用会话配置，避免模型配置漂移。
  - Skill 使用渐进披露，将命令清单放入 reference。
  - 明确本地测试、Deterministic、Live、Release 和 Apply 的状态边界。
- `ADR Required`: `No`。未改变系统权威、依赖方向、持久化、安全信任边界或 Apply 语义。
- `Architecture Document Delta`: `None`。运行时架构事实未改变。
- `Outcome`: `Approved`

## Final Plan

按 Draft Plan 实施，并以以下条件作为本切片验收：

1. Agent TOML 可被标准 TOML 解析器读取，包含 `name`、`description`、`developer_instructions`。
2. Skill 通过 Codex `quick_validate.py`。
3. `openai.yaml` 可解析，且默认提示显式引用 `$software-delivery-engineer`。
4. 所有新增内容均为新路径，不覆盖工作树中已有未跟踪的 Product Manager Agent/Skill 或产品文档。
5. 最终状态与 diff 被复核；未运行的产品测试明确标注，因为本变更不触及产品代码。

## Implementation

- 新增项目级自定义 Agent。
- 新增仓库级 Skill、UI metadata 和 CI 对齐验证 reference。

## Architecture Reconciliation

本地结构校验已完成：Skill 通过 `quick_validate.py`，Agent TOML 通过 Python `tomllib` 解析，UI metadata 通过 YAML 解析，且 reference 链接存在。

本变更不产生运行时架构文档增量，也不构成产品测试、Agent-Team-OS Live Release、Apply 或远端交付证据。仓库原有分支与未跟踪文件保持在位；本轮未执行 Commit、Push、PR、Merge 或同步远端。
