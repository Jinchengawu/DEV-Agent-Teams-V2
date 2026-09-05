# 真实交付的浏览器检查点

`scripts/browser_live_delivery_checkpoint.py` 连接已经运行的真实服务，观察指定 Delivery，
展示产品页面并记录可供审阅的 Gate 主题、Artifact 定位和冻结身份。
它不创建 Delivery、数据库、远端仓库或评测账号，不发送任何业务决定。
检查点和退出码均不构成正式 Live Release Report。

## 运行前提

- 操作者已取得独立评测账号，指定 URL、Project ID、Delivery ID 和完整 Product SHA。
  用户名与密码只由本次会话环境注入，不写入命令参数、文件、日志或报告。
- 服务已冻结干净 Product/ACWM Build Identity、真实 `codex.cli` 或 `hermes.acp` Binding，
  四个 `external-git` Workspace，以及本次 Pipeline、Method、Profile 和 Knowledge 身份。
  这些身份检查仅防止连错运行；完整 Runtime 与证据验收由现有 V2 Gate 负责。
- 同一组真实远端只由一个权威数据库管理。Project Lease 不提供跨数据库互斥；
  禁止把第二个数据库指向仍被其他运行持有的四个远端。
- 旧 Delivery 的 Build Snapshot 不得改写。新 Product SHA 必须建立新 Delivery。
- 本机已安装项目开发依赖与 Playwright Chromium；服务已有账号且完成初始化。
  HTTP 仅允许 loopback；其他服务地址必须使用 HTTPS，不允许 URL 内嵌凭据。

## 观察和续跑

在可信终端中安全注入 `AGENT_TEAM_OS_TEST_USERNAME` 与 `AGENT_TEAM_OS_TEST_PASSWORD`。
不要在复制给其他人的命令或终端录屏中填写密码值。运行：

```bash
uv run python scripts/browser_live_delivery_checkpoint.py \
  --url http://127.0.0.1:8765 \
  --project-id <project-id> \
  --delivery-id <delivery-id> \
  --expected-product-sha <40位Product-SHA> \
  --checkpoint .agent-team-os/evaluation/checkpoints/delivery.json \
  --screenshot .agent-team-os/evaluation/checkpoints/delivery.png \
  --headed
```

默认读取一次后退出。`--wait-seconds 300` 可在五分钟内持续只读观察，
状态或证据变化时更新检查点；上限一小时，按 Ctrl-C 结束不会取消产品运行。
相同命令再次运行会读取既有检查点，核对 URL、Project、Delivery、冻结身份及版本单调性。
换 Delivery 使用新检查点路径；不通过删除检查点来规避身份不匹配。

驱动创建独立、临时浏览器上下文，不保存 cookies、storage state、token 或浏览器 trace。
观察窗口禁止除登录外的所有写请求；真实决定应在操作者的常规产品浏览器窗口完成。
密码缺失或登录失败时退出，不创建账号、不截图，也不打印可能包含填值的 Playwright 异常。
截图只在成功登录后进行；页面仍含密码输入框或本次密码值时拒绝截图。

| 观察状态 | 必须审阅的具体产物 | 操作者下一步 |
| --- | --- | --- |
| Plan Gate | `gate_id`、`artifact_id`、`subject_sha256`、Revision；Delivery API 的 `/requirements` 与 `/task`；产品 UI 的计划与固定验证命令 | 在常规产品 UI 查看完整正文并决定批准或拒绝 |
| Design Gate | 当前 Design Candidate、Diff SHA、Verification SHA、Review ID，以及 Gate Subject | 查看当前候选与证据，再经产品 UI 决策 |
| Release Gate | `ReleaseBundleV2` 的精确 Hash、四仓 Candidate/PR/验证/Review 引用和冻结基线；Subject 必须匹配 Bundle | 审阅 Forward-only 影响，再经产品 UI 决策 |
| `needs_attention` | 原 Bundle、Apply Attempt、已成功 Receipt、错误码和 Release Health API 链接 | 依产品合法路径恢复；本驱动不执行 `resume-forward` |
| `completed` | 与 Delivery 对应的 active Manifest、四仓 Receipt 和 Evidence 链接 | 运行正式 Readiness/Live Acceptance，不能用浏览器观察替代 |

检查点仅保存允许列出的元数据、Hash、ID 和产品链接。Artifact 正文、需求自由文本、
Provider 配置和 Evidence payload 不复制进报告；通过 `api_url + json_pointer` 与 UI 链接
定位具体产物。每次采样覆盖该 Delivery 的检查点；审批历史仍以产品 Gate/Event 为权威。

退出码：`0` 表示观察到匹配 active Manifest 的 completed；`20` 表示待人工决定；
`21` 表示仍运行或需要处理的终态；`1` 表示输入、身份、证据或读取失败。
所有检查点的 `formal_release_acceptance` 都是 `not_evaluated`。

## 正式交接

在相同干净 Product Revision 上分别取得核心浏览器、Deterministic 和 Live Gate 证据。
Knowledge R2 还需真实 Feishu、Index/Ollama、七个 Stage Context、五个 Workcell、Citation
与结果接纳证据。刷新 Readiness 后，用现有 `knowledge-live-gate` 核验真实 completed Delivery。
交接索引关联报告哈希、SHA、Build/Pipeline 身份和运行 ID；
只有正式报告 `FAIL=0`、`WARN=0`、`skipped=0` 才能宣布相应版本验收完成。

本脚本的回归测试和临时模拟服务浏览器烟测仅证明观察驱动行为，不是 Live Agent 证据。

## 实施前架构审查

- Architecture Impact: None
- Findings: 既有四仓与 Knowledge 浏览器驱动使用 Deterministic Adapter 并自动完成测试决定，
  不能用于真实人工 Gate；本检查点只读投影已有产品权威。
- Required Revisions: 冻结身份失败关闭；仅保存允许列出的 Artifact 链接和哈希；
  登录之外只允许读取；禁止自动 Plan/Design/Release/resume/cancel；
  明确检查点不等于验收；保留人工 Gate 与单数据库独占远端约束。
- ADR Required: No
- Architecture Document Delta: None
- Outcome: Approved

审查经协调者确认后实施；不改变系统权威、状态机或安全边界，不新增架构总览条目。

### P0-03/04 确定性浏览器入口兼容审查

- Architecture Impact: None
- Findings: 最新 UI 为每个 Workcell 分别显示 Method 与 Verification Profile，
  并在 Plan Gate 展示责任、在当前 Workcell 打开已登记的 Diff 与原始 Review；旧驱动未覆盖这些行为。
- Required Revisions: 按语义分别核对五个 Method/验证方案；审批前检查每仓责任与真实产物 Modal；
  最终 API 核对 Review Scope 与获批 Plan 的责任和 Hash 关联；先构建最新 Console 并验证 HTTP 静态资源一致；
  登录失败或密码仍可见时禁止截图；保留真实失败证据，不放宽产品断言。
- ADR Required: No
- Architecture Document Delta: None
- Outcome: Approved

该检查使用独立临时数据库、四个本地 Bare Remote 和 Deterministic Adapter；
其自动审批仅属于隔离测试，不能用于真实 Live 运行。

### P0-07 确定性浏览器原生收据

基础四仓入口支持 `--receipt`；R2 入口为
`scripts/browser_feishu_knowledge_e2e.py --gate-c --receipt <输出路径>`。
两个入口会创建项目与 Delivery，并批准隔离测试的业务 Gate，只能连接本次新建的
`agent_team_os.gate_app:app` 临时服务。真实服务继续使用本文的只读检查点入口。

运行前先提交产品、工具与文档，构建该 Revision 的最新 `console/dist`，再启动无热重载的
临时服务。临时目录只复制公开且锁定的 Method Pack，使用四个独立本地 Bare Remote；
不复制旧 Live 数据库、账号或凭据。R2 临时服务启用以下三个 Flag，Feishu/Embedding
外部边界分别保持 `DeterministicGateTenantKnowledgeResolver` 与
`DeterministicGateEmbeddingPort`：

- `AGENT_TEAM_OS_FEATURE_FEISHU_TENANT_SYNC_V1=1`
- `AGENT_TEAM_OS_FEATURE_KNOWLEDGE_HYBRID_INDEX_V1=1`
- `AGENT_TEAM_OS_FEATURE_DELIVERY_KNOWLEDGE_CONTEXT_V1=1`

将随机评测密码仅注入浏览器子进程的 `AGENT_TEAM_OS_TEST_PASSWORD`，使用本次实际服务 URL
和数据目录。例如：

```sh
.venv/bin/python scripts/browser_feishu_knowledge_e2e.py \
  --url http://127.0.0.1:<临时端口> --data-dir <临时数据目录> --gate-c \
  --receipt .agent-team-os/reports/<Revision>/r2-browser.json \
  --screenshot .agent-team-os/reports/<Revision>/r2-browser.png
```

收据模式在运行前后核对干净 Product/ACWM Build，以及 HTTP 实际返回的每个静态文件字节；
Delivery 冻结 Build 必须相同。全部 UI、Scope、Review、Apply、Wiki、Console 断言完成后才
写出 `core-browser-run-receipt-v1`。R2 另外读取并核对七个实际 Context Artifact 的 Hash、
授权纪元和 Citation，保留五个 Workcell ID；QA Preparation 必须无 Candidate，且产物接纳通过。
连续同页运行后知识面板也必须显示实际 Context，刷新页面不能代替该断言。

`--receipt` 禁止同时使用 `--state`。开始时仅清除本次指定收据，任何异常都使其保持不存在；
未指定收据时可做 dirty 本地回归，但截图和退出码不能升级为正式证据。结束后由临时服务的
启动者回收服务及浏览器进程。三轨报告的范围与关联规则见
[交接证据索引](DELIVERY-HANDOFF-EVIDENCE.md)。

本切片的六字段最终架构审查保留在
[P0-07 Final Plan](../plans/2026-09-05-DELIVERY-CLOSURE-PLAN.md#p0-07原生浏览器收据与交接引用索引-final-plan)：
Architecture Impact: Local；Findings: 基础/R2 范围不同，报告只表达已有权威；
Required Revisions: 原生严格收据、七 Context 与 Artifact-only QA、密码与状态边界；
ADR Required: No；Architecture Document Delta: None；Outcome: Approved。
