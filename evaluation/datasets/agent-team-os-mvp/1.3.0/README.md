# Agent-Team-OS MVP 评测数据集 1.3.0

> 本文档为默认中文版。[English version](README.en.md)

该数据集是 Agent-Team-OS 工程自有的合成小样本，用于验证 Evaluation Harness 与本地控制面探针。
它不是官方 BFCL 或 GAIA 数据集，不得用于声称榜单成绩或真实 Agent 性能。

## 内容与用途

- 5 条 BFCL-compatible ToolCall 用例；
- 3 条 GAIA-compatible 类型化答案用例，覆盖难度 Level 1/2/3；
- 1 条全链路 Data Generation 配对比较契约；
- 1 条本地 GraphRun/SQLite/HTTP 恢复探针契约。

`fixture_output` 被明确标记为 Deterministic，只能在 `offline` 模式中消费。Live 执行在调度前会移除
该字段；真实 Runtime 未接入时，Case 和 EvaluationRun 为 `blocked`，EvaluationReport Gate 为
`not_run`。Standard 使用固定随机种子，在并发度
1/4/8 下展开这 10 条基础用例；每个并发度执行 3 轮，首轮预热，后两轮进入统计。
展开规则是按 `index % 10` 循环复制为 100 条工作负载，再使用记录的 Seed 打乱；因此 Standard 最终产生
`100 × 3 个并发度 × 2 个计量轮次 = 600` 条观测。

## 来源、许可与版本策略

用例由 Agent-Team-OS 工程自行编写，按仓库许可证发布。任何语义用例、预期答案、评分规则或 Schema 变更，
都必须发布新数据集版本，更新 `manifest.json` 中的文件哈希、评分器兼容性测试，并重新完成三轮校准。

## 文件说明

- `manifest.json`：锁定 Suite 身份、版本、用例分布、Schema 和文件 SHA-256；
- `cases.jsonl`：带稳定 Case ID 的评测用例；
- `schema.json`：数据结构与字段约束；
- `README.md`：默认中文数据集卡；
- `README.en.md`：英文数据集卡。
