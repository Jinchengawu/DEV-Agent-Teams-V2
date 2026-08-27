# DEV-Agent-Teams V2 / Agent-Team-OS

> 本文档为默认中文版。英文伴随文档将使用 `README.en.md`。

本仓库是 DEV-Agent-Teams 的 Clean-room V2 实现。Agent-Team-OS 的产品定位是交付控制面，
而不是多 Agent 聊天界面。

## 当前架构

![Agent-Team-OS 当前架构深色版](docs/assets/architecture/agent-team-os-current.dark.png)

需要中文节点说明时，请查看[中文节点架构图](readme-cn.md)。

首个产品闭环刻意保持狭窄：

```text
后端请求
  -> Hermes PM 需求
  -> Hermes Project Admin 任务契约
  -> 审批门禁
  -> Codex 候选变更
  -> 产品验证
  -> 审批门禁
  -> 原子应用或拒绝
```

架构所有权：

- ACWM：跨 Stage Journey、Workflow/Capability 解析与全局 Gate。
- AgentScope：Stage 内部通信和 Agent 组合。
- Hermes：PM 与 Project Admin 角色 Instance。
- Codex：代码执行 Capability。
- Agent-Team-OS：工作区安全、Evidence、策略、决策和产品 API。

V2 是全新项目。旧 DEV-Agent-Teams 保持不变，不是可直接复制的源代码树。

## V0.3 多 Pipeline DAG/LOOP 发布候选版

在 V0.2 控制面之上已实现：

- 创建并保留多个可独立配置的 Pipeline；
- 使用 React Flow 编辑语义 DAG 依赖和条件边；
- 发布不可变 Revision 前验证拓扑和产品兼容性；
- 激活指定 Revision，并将每个新 Graph Delivery 固定到 Definition、Binding Snapshot 和 SHA-256 指纹；
- 执行权威 ACWM GraphRun，不再把 Graph 转换回固定 Delivery 序列；
- 并发执行已就绪的独立 AgentScope 角色 Stage，同时将 Git 副作用串行化；
- 在有界修复 LOOP 中执行代码交付，保留每轮迭代与 Body Node Evidence；
- 为每次代码尝试保留不可变 Candidate Ref，并计算最终 Base-to-Candidate Diff；
- 进程重启后恢复已完成 GraphRun，并对失败/取消保持 fail-closed；
- 要求 Deterministic 和 Live 发布门禁证明 Pipeline Revision、Graph 指纹、完成的 GraphRun、Candidate、
  Diff、Verification 和重启 Evidence。

内置 `backend-delivery` 已是 Schema-v4 DAG，代码修复 LOOP 最多执行三轮。产品兼容性要求
PM、Project Admin 和 Backend Capability，以及且仅有一个 Plan Gate 和一个 Candidate Gate。
LOOP Body 内的嵌套 Human Gate 当前会被明确拒绝。

ACWM v0.5.0 Runtime 在 `pyproject.toml`、`uv.lock` 和 `config/framework-lock.json` 中固定到
已发布 Commit `65acf7f`。
Demo Readiness 还会独立根据 `config/framework-lock.json` 检查导入源，修订漂移时 fail-closed。

## V0.2 控制面基线

已实现：

- 保留 V0.1 真实 Git 交付闭环和 CAS Apply 保证；
- 注册 `hermes-acp`、`hermes-http` 和 `codex-cli` 执行 Instance，不持久化秘密值；
- 健康检查 Instance，并将健康、已启用的 Instance 绑定到 ACWM Capability；
- 克隆、重排、验证并发布不可变 ACWM Journey Revision；
- 将每个新 Delivery 固定到已发布 Journey Revision 和冻结 Binding Snapshot；
- 把 Delivery 状态投影到可重建的六列 Board，仅接受合法命令；
- 归档 Journey、Requirement、Task、Gate、Candidate、Verification 和 Receipt Evidence；
- 通过 SQLite FTS5、内容哈希和来源链接检索可追溯知识；
- 通过 `/v1/events/stream` 暴露持久控制事件流；
- 提供带持久 Operating Map 的 React/Vite 控制台。

当前控制面刻意不实现 RAG、Embedding、AgentScope-native Agent/Team 管理、共享长期记忆、多租户、
云部署或用户仓库 Adapter。这些属于后续里程碑。

## 项目评测

Agent-Team-OS 建立了独立 Evaluation 评测域，用于可重复验证能力、交付质量和控制面性能。
每次运行都会冻结 Pipeline Revision、Deployment 绑定、ACWM 与 Git 修订、数据集及评分器身份；
报告按维度并列展示，不生成误导性综合分。

最新已发布基线：**2026-08-24，suite 1.2.0，offline standard，seed 20260824**。

| 评测维度 | 已评测/总数 | 结果 | 可证明范围 |
|---|---:|---:|---|
| ToolCall / BFCL-compatible | 300/300 | 300 通过 | AST 归一化与 Fixture Trace 评分链路 |
| General Agent / GAIA-compatible | 180/180 | 180 通过 | 类型感知的准精确 Fixture 评分 |
| Data Generation | 60/60 | 60 平局 | 同一冻结对象未产生虚假质量差异 |
| Control Plane | 60/60 | 60 通过 | 本地 GraphRun、CAS、恢复、SQLite 和 ASGI 探针 |

本地控制面观测：

| 指标 | p50 | p95 | p99 |
|---|---:|---:|---:|
| 候选侧 HTTP 时延 | 2.36 ms | 6.29 ms | 9.43 ms |
| 候选侧 GraphRun 总时延 | 8.80 ms | 101.03 ms | 121.81 ms |

- 门禁：`passed`；证明范围：`fixture_harness_only`；官方 Benchmark：`false`。
- Evidence SHA-256：`d9e2019fa6e86f632e0d3d513f04cf7a73d3de55065e05f845919080ead3e2c6`。
- `134 passed, 1 skipped` 是当时的软件测试基线；上述 600 条是评测工作负载观测，分母不同。
- Deterministic Fixture **不能**证明真实模型能力、官方 BFCL/GAIA 排名、独立生成质量、Token/成本
  或生产网络 SLA。未配置 Live Runtime 时，Case 状态为 `blocked`、Run 状态为 `blocked`、
  Report Gate 为 `not_run`；不同对象缺少独立 Judge 时保持 `blocked`。

详见[评测方法论](docs/evaluation/METHODOLOGY.md)、
[版本化数据集卡](evaluation/datasets/agent-team-os-mvp/1.3.0/README.md)、
[脱敏历史基线](docs/evaluation/results/2026-08-24-offline-standard.md)、
[PR/Push CI](.github/workflows/ci.yml) 和[手动完整评测](.github/workflows/evaluation.yml)。

发布基线属于历史 Suite 1.2.0；当时用例内嵌在代码中，当前仓库不将其冒充为可重放数据集。
后续可重复验证使用版本化 Suite 1.3.0；修改用例或 Schema 会改变 SHA-256，并要求重新校准。

```bash
# 校验版本化数据集，不修改正式产品数据。
uv run agent-team-os-dev eval validate-dataset

# 针对已初始化的本地产品数据库运行。
uv run agent-team-os-dev eval run --mode offline --profile standard --seed 20260824
```

## 保留的 V0.1 保证

- 创建 Backend Delivery，并在 Plan Approval 前停止；
- 通过乐观版本保护决策；
- 仅在 Plan 审批后执行，并暴露不可变 Candidate Evidence；
- 通过 SQLite 持久化并恢复 Delivery Snapshot；
- 拒绝 Candidate 时不产生 Apply 副作用；
- 独立验证 Candidate，完成前要求精确原子 Apply Receipt；
- 对 ACWM、AgentScope、Hermes 凭证和 Codex 登录进行 fail-closed Readiness；
- 将 ACWM v0.3 固定到 Commit `b79e671`；
- 解析并指纹化权威 ACWM `backend-delivery` Journey；
- 立即返回 `202`，在后台推进 Planning/Execution/Apply；
- 将两次审批绑定到 ACWM Gate Subject Hash 和乐观版本；
- 在隔离 Git Worktree 中以 `workspace-write` 执行 Codex；
- 拒绝空、超范围、含秘密或测试失败的 Candidate；
- 创建不可变 Candidate Ref、统一 Diff Hash 和固定 Unittest Evidence；
- 仅通过 `git update-ref <candidate> <base>` Compare-and-swap 执行 Apply；
- 重启后恢复 Approval 状态，对中断执行 fail-closed；
- 暴露 Delivery History、Cancellation、Sandbox Reset 和 Release-gate Report。

在 Hermes Instance 配置完成前，Codex 可通过 AgentScope Role-turn 和 ACWM Codex Capability Adapter
模拟 Hermes PM/Admin。该 Evidence 始终标记为 `codex-simulated-hermes`，不得报告为真实 Hermes 调用。
无效结构化 Planning 仅重试一次，之后 fail-closed。Deterministic Adapter 仅限测试，Evidence Identity 为
`deterministic-test`。

## 开发验证

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
uv build

cd console
node ./node_modules/typescript/bin/tsc --noEmit
node ./node_modules/vitest/vitest.mjs run
node ./node_modules/vite/bin/vite.js build

# 显式真实 Codex Smoke Test（只读 Planning，需要 Codex 登录）
AGENT_TEAM_OS_LIVE_CODEX=1 uv run pytest \
  tests/integration/test_live_codex_simulated_planning.py -q
```

## 运行产品

产品通过 AgentScope 和 ACWM 使用 Codex 模拟 Hermes PM/Admin，代码执行使用真实 Codex CLI
`workspace-write` Turn。V0.3 的目标是 `.agent-team-os/workspaces` 中的内置标准库 Python Backend Bare Repo；
用户仓库当前明确不在范围内。

```bash
uv sync --extra dev --extra live
pnpm --dir console install --frozen-lockfile
uv run --extra live agent-team-os demo
```

打开 <http://127.0.0.1:8080/>。数据和不可变报告保存在 `.agent-team-os/`。

## 发布门禁

```bash
# 真实 Git 生命周期，使用 Deterministic Model Boundary
uv run --extra live agent-team-os gate

# 真实 Codex Planning 与真实 Codex 代码执行
uv run --extra live agent-team-os gate --live

# 同时运行两类门禁；任一失败都返回非零退出码
uv run --extra live agent-team-os release
```

JSON 和 Markdown 报告包含 DEV/ACWM Revision、Pipeline Revision、Graph 指纹、GraphRun Identity/Status、
Candidate Revision、Diff SHA-256、Verification Exit Code、Identity 和 Evidence Hash。只有两类门禁都清洁，
且引用相同 DEV 和 ACWM Revision 时，`release` 才返回成功。

Settings 页面和 `/v1/release-gates/latest` 在最新 Evidence 缺失、损坏、超过 24 小时、Hash 无效、
Identity 无效、不完整或 Revision 不匹配时报告 `unknown` 或 `failed`。Deterministic Report 还必须证明
完整 Browser 闭环和进程重启恢复。
