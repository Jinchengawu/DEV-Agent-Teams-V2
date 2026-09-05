# 四仓 R2 交接证据索引

`scripts/check_delivery_handoff.py` 只关联已有原生报告。`reference_check=consistent` 表示三轨引用
与目标相容，不能代替 Release Report、业务 Gate 或当前远端状态。工具不会运行 Agent、访问业务服务、
批准 Gate 或修改产品数据库。

正式交接的目标为 `four-repo-r2-alpha`。三个输入必须绑定同一干净 Product Revision 和 ACWM Revision，
各自原生 `fail=0`、`warn=0`、`skipped=0`，Browser 与 Live 的 Build Identity 必须一致：

| 输入 | 原生内容及范围 | 原生 Hash |
| --- | --- | --- |
| `--core-browser` | `core-browser-run-receipt-v1`，R2 scenario，七个 Context 和五个 Workcell 的完整实际浏览器断言 | `receipt_sha256` |
| `--deterministic-gate` | 既有 `GateReport(kind=deterministic)`，保留其单后端/多 Pipeline 基线范围 | `evidence_sha256` |
| `--live-release` | `release-acceptance-report-v2` / `feishu-knowledge-delivery-v1`，现有四仓 R2 完整检查集 | `report_sha256` |

浏览器 Runtime 是 `deterministic-model-boundary`，不能写为 Live。Delivery 上的 Planning/Evidence
身份和可能为空的 Execution 身份原样保留；每个 Workcell 的实际 Runtime 另行断言。
Live Report 本身没有 Runtime identity 字段；索引仅以 `planning_adapter_verified` 和
`execution_adapter_verified` 引用已通过的 Codex/Hermes Binding 检查含义，不补造 Runtime identity。
各轨 Project、Delivery、Pipeline 和 Candidate 可以不同，索引保留实际值，不合并成一次运行。

原生浏览器驱动的严格 `--receipt` 模式会在启动时使本次指定收据路径失效。
完整 UI、产品证据、Console 无错误、当前 HTTP Bundle 与本机 dist 一致、运行前后干净 Build 一致
都通过后才原子生成成功收据；失败不会保留该路径上次的成功输出，其他历史报告仍保留。
R2 入口复用 `scripts/browser_feishu_knowledge_e2e.py --gate-c`；基础四仓入口生成的
`knowledge_scope=null` 收据仅可归档，不能满足 R2 交接。
缺少 Knowledge 配置、部分 Stage 成功、Readiness ready、Checkpoint、截图和 CLI exit 0 都不能补足收据。

先提交并冻结工具、产品及文档 Revision，再执行三轨验收。生成的索引写入忽略的报告目录，避免改变
已验收产品 SHA。下面路径需要替换为本次实际原生报告；不自动选择“最新”文件：

```sh
.venv/bin/python scripts/check_delivery_handoff.py \
  --product-revision <完整ProductSHA> \
  --acwm-revision <完整ACWMSHA> \
  --core-browser .agent-team-os/reports/<R2浏览器收据>.json \
  --deterministic-gate .agent-team-os/reports/<确定性报告>.json \
  --live-release .agent-team-os/reports/<V2Live报告>.json \
  --output .agent-team-os/reports/<版本>/handoff-index.json
```

退出码 `0` 仅表示引用检查 `consistent`；`2` 表示 `incomplete`、`invalid` 或调用错误。
缺轨/基础浏览器缺 R2 为 `incomplete`，错 Revision/Build、内容 Hash 不符、未知 Schema/检查码或
失败报告为 `invalid`。索引保留明确问题及原生 Hash，另外记录文件字节 Hash用于核对所引用的文件。
这些 Hash 表达内容完整性，不替代原生 runner 的实际执行断言。旧报告不会自动迁移或补填资格。

交接还需按 P0-05/P0-06 保留四仓 Candidate、PR、Verification、Review、Bundle、Apply Receipt、
Manifest、health 和隔离故障验证的原生证据。索引只引用已有 Report 中的证据 Hash，不能创建缺失证据。
独立评测账号仅记录已安全交付这一事实；密码不得进入索引、报告、截图或 Git。
