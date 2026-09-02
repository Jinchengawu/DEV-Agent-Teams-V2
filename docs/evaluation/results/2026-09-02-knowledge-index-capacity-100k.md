# Knowledge Index 100,000 Chunk 容量基准

> 证据等级：`deterministic_capacity_benchmark`。本报告不是官方 Benchmark，不是生产 SLA，
> 也不证明真实飞书或 Ollama 可用。

| 项目 | 实测值 |
|---|---:|
| 机器 | `Apple M1 Max; 32.0 GiB RAM; Darwin 25.5.0` |
| Python / SQLite | `3.12.11` / `3.49.1` |
| Document / Chunk | 1 / 100,000 |
| Vector Dimension | 1024 |
| Index Size | 607,920,128 bytes |
| Build | 11.321 s |
| Restart Cold Integrity | 288.015 ms |
| Query p50 / p95 / p99 | 511.538 / 605.115 / 613.260 ms |
| Peak RSS | 425,705,472 bytes |
| Capacity Status | `warning` |

## 边界

- Dataset 和 1024 维 Embedding 均为 Deterministic Fixture。
- Query 观测包含真实 SQLite FTS5、`sqlite-vec` cosine 扫描和 RRF，
  但不包含网络与真实模型生成时间。
- Source Revision 为 `cfe597c05b3b0c65af57bf12d14b7f802fe7899f`，
  执行时工作树脏状态为 `true`。
- JSON 原始结果见
  [`2026-09-02-knowledge-index-capacity-100k.json`](./2026-09-02-knowledge-index-capacity-100k.json)。
