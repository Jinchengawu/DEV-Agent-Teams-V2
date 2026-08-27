# Agent-Team-OS 评测方法论

> 本文档为默认中文版。[English version](METHODOLOGY.en.md)

## 目标与证据边界

Evaluation 是独立的产品域。ACWM 继续拥有跨 Stage 编排，AgentScope 拥有 Stage 内部组合，
Hermes 拥有 PM/Project Admin 角色智能，Codex 拥有受控代码执行，产品代码拥有权限、
Evidence、Verification 和 Apply 策略。Evaluation 只负责调度用例、采集观测、评分对照对象并生成
不可变报告；它不复制 ACWM Runtime Contract，也不修改 Delivery 状态语义。

每次评测都冻结以下信息：数据集与评分器 SHA-256、Pipeline Revision 及指纹、Deployment 绑定、
Git/ACWM 修订、候选/基线身份、随机种子、并发度、超时和成本上限。Deterministic Fixture 证据始终标记为
`fixture_harness_only`，不得当作官方成绩或真实 Agent 能力证据。

## 评测维度与评分

| 维度 | 输入与证据 | 评分方式 | 当前 Offline 边界 |
|---|---|---|---|
| ToolCall | 标准化工具 Trace | AST 归一化精确匹配；并行调用按多重集合比较 | 仅 Fixture Trace |
| General Agent | 类型化最终答案 | 文本、数字、日期和列表的准精确匹配 | 仅工程自有样例 |
| Data Generation | Requirement 到 Apply Receipt 的完整链路 | 盲化配对 win/tie/loss；不同对象要求独立 Judge | 仅同对象 tie |
| Control Plane | 真实本地 GraphRun/SQLite/ASGI 操作 | 百分位、状态与恢复不变量 | 不包含网络/TLS/反向代理 SLA |

配对指标固定为：

```text
win_rate = wins / (wins + losses)
non_loss_rate = (wins + ties) / total
```

Tie 和所有分母必须显式展示。二元正确率报告 Wilson 95% 置信区间。人工复核包含所有必审失败/冲突案例
与固定种子抽样；只有真实导入人工结果后，才计算一致率与 Cohen’s kappa。

## Profile 与可重复性

| Profile | 基础用例 | 并发度 | 重复次数 | 计入统计的观测数 |
|---|---:|---|---:|---:|
| smoke | 10 | 1 | 1 | 10 |
| standard | 100 | 1/4/8 | 每个并发度 3 轮，首轮预热 | 600 |
| live | 显式限额 | 默认 2 | 由 Runtime 控制 | 未配置时不可用 |

Standard 对候选与基线使用相同数据集、种子和环境。探针执行顺序由记录的种子随机化，以减少顺序偏差。
Git 副作用保持串行；控制面探针使用独立 SQLite 数据库和报告工作目录。

Standard 的 100 条工作负载由 10 条版本化基础用例按 `index % 10` 确定性循环展开，因此每条基础用例
在每个并发度和每个重复轮次中出现 10 次；展开后再由记录的 Seed 打乱。保留两个计量轮次后，
分母为 `100 × 3 个并发度 × 2 轮 = 600`，按数据集 5/3/1/1 分布得到 300/180/60/60。

## 校准与发布门禁

连续三次 Offline Standard 同对象自比较，会根据中位数和中位绝对偏差生成不可变 Calibration Profile。
校准完成前状态为 `calibrating`；下一次配对运行才可能得到 `passed` 或因门禁失败。数据集、
评分器或被测对象指纹变化时，必须产生新的校准身份；旧 Suite 不能隐式校准新 Suite。

校准后的主要失败条件：

- 自动正确率或成功率下降超过 2 个百分点；
- p95 时延、平均成本或平均工具调用数恶化超过 20%；
- 错误率增加超过 1 个百分点，或恢复率下降超过 2 个百分点；
- 生成质量 loss rate 超过 10%，且人工复核确认退化。

秘密泄露、Evidence 哈希无效、越权副作用、错误 Apply 或伪造 Evidence Identity 立即失败。

状态语义坚持 fail-closed：

- `passed`：所有适用且已校准的门禁通过；
- `failed`：适用的正确性、安全、可靠性或回归门禁失败；
- `calibrating`：已有证据，但三轮校准尚未完成；
- `blocked`：缺少必需 Runtime、凭证或独立 Judge 证据；
- `not_run`：请求的 Live 维度未执行，不能计为通过；
- `unsupported`：冻结 Runtime 缺少必需 Feature，用例不进入分数。

Live Runtime 未配置时，三个层级分别记录：Case 为 `blocked`、EvaluationRun 为 `blocked`、
EvaluationReport 的 Gate 为 `not_run`。`not_run` 是报告门禁结论，不与 Case/Run 的阻塞状态混用。

## 数据集生命周期

规范数据集位于 `evaluation/datasets/<suite>/<version>`。Manifest 中的哈希锁定 JSONL 用例和 JSON Schema。
运行前校验稳定 Case ID、精确维度分布和各维度评分契约。缺少文件、ID 重复、Schema 漂移、字节变化或
分布不一致都会中止评测。

修改任一用例、预期输出、评分规则或 Schema 时，必须：

1. 发布新数据集版本，并明确评分器兼容性；
2. 重新生成 `manifest.json` 中的文件哈希；
3. 更新数据集卡与契约测试；
4. 完成三次新 Standard 校准；
5. 只有完成证据审阅后，才发布新的脱敏基线。

官方 BFCL/GAIA 数据必须使用独立授权、固定版本和官方评分器身份。工程自有 compatible 用例不得改名为
官方 Benchmark 结果。

2026-08-24 发布的 Suite 1.2.0 基线是历史快照：当时用例内嵌在代码中，仓库未提供可重放的 1.2.0
数据集目录。后续重复验证的规范起点是版本化 Suite 1.3.0；不得用 1.3.0 结果伪称精确重现 1.2.0。

## 本地复现与 CI

校验数据集，并针对已初始化的产品数据库运行 Smoke：

```bash
uv run agent-team-os-dev eval validate-dataset
uv run agent-team-os-dev eval run --mode offline --profile smoke --seed 20260824
```

[PR/Push CI](../../.github/workflows/ci.yml) 只运行 `eval validate-dataset`、Schema/哈希契约、Ruff、Mypy、
Pytest、迁移校验与构建，不执行 Standard 负载。

[手动完整评测](../../.github/workflows/evaluation.yml) 在 GitHub Actions 的 `Manual evaluation` Workflow 中触发。
该 Workflow 为评测显式设置临时 `AGENT_TEAM_OS_DATA_DIR`，并在每轮使用 `--bootstrap-fixture`；
该参数拒绝仓库默认产品数据目录。Workflow 执行三轮 Standard 校准，第四轮增加
`--require-gate-passed`，并上传 JSON/Markdown 报告、SQLite Ledger 和校准证据。完整命令参数以
Workflow 文件为准，默认 Seed 为 `20260824`。

Live 评测后续必须显式接入真实 Runtime Identity、凭证、Token/成本上限和网络依赖。在此之前，
Case 和 EvaluationRun 保持 `blocked`，EvaluationReport Gate 为 `not_run`，且不阻断 Offline 发布门禁。
