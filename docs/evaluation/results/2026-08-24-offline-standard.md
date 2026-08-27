# Offline Standard 评测基线——2026-08-24

> 本文档为默认中文版。[English version](2026-08-24-offline-standard.en.md)

本文档是本地报告 `43303b10-4558-456f-8015-2dbf026d7eb1` 的脱敏、版本化投影。原始不可变
JSON/Markdown 报告保留在内容寻址的本地 Evidence Ledger 中。由于原始报告含有完整 Deployment Snapshot，
因此不进入 Git。

该基线属于历史 Suite 1.2.0，当时用例内嵌在代码中；仓库不提供可重放的 1.2.0 数据集。
当前可重复验证从版本化 Suite 1.3.0 开始，不得将 1.3.0 结果表述为对本基线的精确重放。

| 属性 | 值 |
|---|---|
| Suite | `agent-team-os-mvp` 1.2.0 |
| Profile | offline standard |
| Seed | 20260824 |
| 门禁 | `passed` |
| 证明范围 | `fixture_harness_only` |
| 官方 Benchmark | `false` |
| Evidence SHA-256 | `d9e2019fa6e86f632e0d3d513f04cf7a73d3de55065e05f845919080ead3e2c6` |

## 观测结果

首轮预热不计入统计，最终保留 600 条观测：

- ToolCall：300/300；
- General Agent：180/180；
- Data Generation：60/60，全部为同对象 tie；
- Control Plane：60/60。

候选侧 HTTP 时延为 p50 2.36 ms、p95 6.29 ms、p99 9.43 ms。候选侧 GraphRun 总时延为
p50 8.80 ms、p95 101.03 ms、p99 121.81 ms。

## 解读边界

该结果证明 Deterministic 评分 Harness 和本地控制面探针能够运行。它不能证明真实 Agent 智能、
官方 BFCL/GAIA 性能、独立生成质量或生产网络时延。同目录 JSON 文件保留了机器可读分母和精确值。
