# 下一阶段交付计划：从可评测候选到正式四仓交接

## 1. 状态与执行入口

2026-09-22 执行更新：用户已明确委派 software_delivery_engineer 实施 S0–S6，完成后由 product_manager 验收，主任务汇总。当前 `Implementing`；下面“待委派”是原文档编写时历史状态。具体外部写入和业务决策仍使用已存在的有效范围授权。

- 日期：2026-09-22；状态：`Final Plan / 待产品范围审阅与实际执行委派`。
- `Final Plan` 仅表示本文经过下面的文档架构审查和修订，内容已具备评审结构；不表示需求已获人类批准、代码已实现或业务 Gate 已批准。
- 输入基线：`5395a4bc1a2ed5960c774a7f12bb417f896053a5`。本轮仅新增本文与 [产品文档](NEXT-STAGE-DELIVERY-READINESS.md)，不运行真实交付。
- 产品 Owner：product_manager；预期执行 Owner：software_delivery_engineer；验收 Owner：主任务；环境/权限与发布决策 Owner：交付管理员/项目负责人。
- 实施要求沿用 [AGENTS.md](../../AGENTS.md)、[架构总览](../architecture/ARCHITECTURE.md)、[交接 Runbook](../runbooks/DELIVERY-HANDOFF-EVIDENCE.md)。

## 2. Draft → Architecture Review → Revise → Final 记录

### Draft Plan

基于已有实现，安排制品复现、依赖资格、R2 浏览器、真实四仓交付、恢复验证与交接证据。仅提出必要缺陷修复，不预先承诺新的产品域、数据表、Runtime 或页面。原先“先得到正式 Live Report 再 Apply”的顺序存在循环风险，需要校正。

### Architecture Review（本轮文档变更）

| 必需字段 | 结论 |
|---|---|
| Architecture Impact | `None`：本轮只写两份产品/计划文档，沿用已接受边界；不预先批准未来代码变化 |
| Findings | 产品仍是交付控制面；ACWM 跨 Stage、产品 Workcell/Verification/Approval/Apply、Runtime Attempt 的权威不变。计划不新增跨模块依赖或表、不复制事实源；Snapshot/Hash/CAS 与四仓隔离继续生效。并发/恢复保持现有 bounded Loop、Lease、Forward-only；历史/Legacy 不改写。只读验收必须在真实 Apply 完成后；Deterministic、真实 local-PR 与正式 Live 分层。公开 API/UI 与原生报告提供可观察性；秘密不进入交接材料 |
| Required Revisions | 分为 A 候选、B 真实执行、C 事后验收；显式标注历史结果未在最终 SHA 重验；冻结 Planning Binding 而非强制 Hermes；将文档矛盾对账列入工作；定义每切片依赖、Owner、失败退出与证据 |
| ADR Required | `No`：本轮没有改变权威、依赖方向、数据/恢复、安全信任、Release 或外部集成策略 |
| Architecture Document Delta | 无；保留本表即可。未来如缺陷修复改变上述边界，必须先审查具体修订并按需形成 ADR/架构增量 |
| Outcome | `Approved`，仅针对本轮文档方案的架构审查；不是业务 Gate、Release 决策或实施授权 |

### Revise Plan

已将正式 Live Report 移至 completed 后；将环境准备、写入资格/权限状态、真实 Agent/PR/Apply 与只读报告动作区分；增加 NR 追踪、失效重验规则、授权范围记录和历史文档对账。当前不再把“没有 Live Report”作为该次 Apply 必须先取得该报告的循环条件。

### Final Plan 与 Architecture Reconciliation

以下 S0–S6 为定稿结构。本轮 Implementation 仅指创建两份文档；对账结果为无架构变化，不改架构总览。未来每个工程修复先按 Draft → Architecture Review → Revise → Final → Implementation → Reconciliation 处理实际影响；本表的 `None/Approved` 不能覆盖未来修复。

## 3. 垂直交付切片

### S0：可审阅的交付基线与运行边界

- 用户价值：负责人知道要交付哪个候选、验证哪个环境，以及谁有权推进。
- Owner：主任务/product_manager；工程收集实现依据，管理员提供环境清单。
- 依赖：本产品文档审阅；当前 Git/锁/运行实例身份只读核对。
- 工作：登记 Product/ACWM/配置/Method/Provider/工具资格身份；核对旧文档中的分支、dirty、取消行为、验证方案和真实闭环叙述，保留历史并注明被哪条新证据取代。登记目标 Project、四仓及知识源的受控标识、授权范围和负责人；敏感详情不复制到正式报告。
- 验收：NR-03；每个依赖均有“当前已证实/历史未复测/未知”状态与来源，没有无主阻塞；正式版本号尚未决定时保留待定。
- 退出证据：基线记录、差异清单、具体执行范围记录。没有权限时仍可完成只读清单，不改状态冒充 ready。
- 停止/升级：目标改为生产、公网或新的 Runtime 边界时交产品/架构重新定义。

### S1：交付制品可独立安装评测

- 用户价值：评测者不依赖开发者 checkout 即可启动和登录。
- Owner：software_delivery_engineer；独立评测：主任务。
- 依赖：S0；干净候选、匹配 wheel/Console/config/Migration/Dataset、隔离 Data Root。
- 工作：使用现有 Builder/Verifier；按 Runbook 安装锁定 Method Pack（记录其网络/安装动作范围）；干净环境启动；提供独立评测账号，执行错误 Root/缺资源/篡改负例。
- 验收：NR-01；Bundle Hash 可重算，启动确实来自指定 Product Root；账号可登录，已有用户数据未被覆盖；只得到 `locally_verified` 范围结论。
- 退出证据：Manifest、构建安装记录、运行身份、登录与安全交付事实、负例输出。
- 停止/升级：资源泄漏、制品版本混合、Root 静默回退或需绕过锁；修复后重新构建候选。

### S2：R2 准备度可解释且执行资格有效

- 用户价值：负责人能定位阻塞并在准备完成后继续原目标。
- Owner：工程负责人；外部依赖 Owner：管理员；体验验收：主任务。
- 依赖：S0/S1；四个独立评测仓库、Feishu Tenant/Approved Source、Ollama/索引、Published Pipeline。
- 工作：核对四仓 Verification Profile/Qualification 和工具闭包；旧资格失效时先报告差异，经范围内配置操作再重新资格化。刷新外部权限/Source/Index 属于实际 I/O 与状态写入，不能伪称只读。记录 Codex/Hermes 的冻结 Binding，仅要求被选择 Provider 的实际合同。核对七个 Knowledge Context 和五个 Workcell；保留草稿和权限负例。
- 验收：NR-02/03/08；Readiness 来源统一，UI/API 阻断一致；每个未知已闭合或明确阻塞。`ready` receipt 仍为 `execution_status=not_run`。
- 退出证据：脱敏 readiness、配置/资格 Hash、权限与 Source 新鲜度、UI/API 正反结果、修复清单。
- 停止/升级：只允许 PR merge 的仓库政策与当前 Forward-only 不兼容；不得自行改保护策略。超时或 Provider 不稳定记录真实失败，不静默调大策略。

### S3：同 Revision 本地核心闭环与确定性验收

- 用户价值：独立评测者可复现主路径，正式交接得到前两轨原生证据。
- Owner：工程执行；主任务独立验收。
- 依赖：S1/S2 对应轨道依赖；所有影响运行、工具和文档的改动先提交到最终候选并冻结。
- 工作：在隔离数据运行 R2 `--gate-c` 浏览器严格 receipt 模式，覆盖七个 Context、五个 Workcell、角色、失败/恢复入口；运行既有 Deterministic Gate。补验 v0.5.2 的准备中心/草稿/运行室/Evidence/onboarding，额外证据不能替代 R2 原生收据。
- 验收：NR-01/02/08 与 NR-07 前两轨；两条原生轨道各自零容差，Build/Revision 正确；严格 receipt 失败不得保留本次路径的旧成功值。
- 退出证据：`core-browser-run-receipt-v1`、`GateReport(kind=deterministic)`、体验补充证据。Browser 标为 `deterministic-model-boundary`。
- 停止/升级：任何失败、WARN、skip、旧 SHA、错误 Bundle 身份均不得进入“已验收”；发现实现缺陷后回到具体修复与重新冻结。

### S4：真实四仓从目标到 completed

- 用户价值：获得实际远端结果和产品记录一致的交付。
- Owner：工程编排；管理员对具体业务 Gate/Apply 决策负责；主任务观察证据。
- 依赖：S0–S3；当前有效的执行/外部上下文传输/候选分支和 PR 外写/业务 Gate/Apply 范围记录。历史许可仅按原范围适用，本计划本身不新增许可。
- 工作：建立 fresh Delivery，保留运行前四仓 SHA；执行真实 Planning、Knowledge Context、五个 Workcell、Verification、Review、真实 GitHub PR；在 Bundle 决策点提交可审阅对象。获授权后使用产品正常 Release 决策/Forward-only 路径，逐仓回读。
- 验收：NR-04/05；四个 reviewed Candidate 与四仓最终 SHA、Receipt、Manifest 一致，Delivery completed、Health healthy、Lease 正确释放。保留合法失败/Repair 历史，不要求所有历史 Attempt 均成功。
- 退出证据：真实 Candidate/PR/Verification/Review/Bundle/审批记录、四份 RemoteApplyReceipt、Manifest、Health 与回读。
- 停止/升级：缺少具体批准范围时停在相应决策点，交付已准备的对象和证据；Base/权限漂移按合同失败关闭；部分成功进入 S5，不把退出进程当作恢复。

### S5：失败恢复与治理责任闭合

- 用户价值：失败后知道已改变什么、谁负责恢复，避免把部分发布当作成功。
- Owner：工程负责人；需要实际恢复命令时由获权管理员执行；主任务审查。
- 依赖：S3 可进行隔离故障验证；S4 若真实失败则保留原 Bundle/Lease/Receipt。
- 工作：在隔离环境验证部分 Apply、回读/Receipt 丢失、第三 SHA 漂移、重启恢复与普通取消拒绝；实际恢复严格按既有 `resume-forward`。不为制造证据主动破坏未授权远端。
- 验收：NR-06；成功仓不重复 Push，不 Force/回滚/换 Bundle；失败时 needs_attention/release_drifted 与 Lease 一致；恢复完成才激活 Manifest 和 completed。
- 退出证据：同 Revision 隔离故障验证与恢复原生记录，标清 Deterministic/Live；真实失败如发生，另保留其完整恢复链。
- 停止/升级：超出原 Bundle 可恢复条件时明确人工协调，不更改数据库终态或删除失败记录。

### S6：只读正式 Live 验收与独立交接

- 用户价值：验收者可从一个索引回读原生事实并作明确结论。
- Owner：主任务独立验收；工程提交证据；项目负责人决定接受或退回。
- 依赖：S4 completed、S5 对应验证闭合、S3 两轨证据；Product/ACWM Build Identity 与报告时一致。
- 工作：执行 `knowledge-live-gate` 检查既有 completed Delivery；用 `check_delivery_handoff.py` 显式引用三条本次原生文件；检查 Hash/Revision/Build 和独立账号安全交付事实，汇总 NR-01–08。
- 验收：三轨各自 `FAIL=0 / WARN=0 / skipped=0`；索引 `reference_check=consistent`；所有必需验收项证据有效且独立审查通过。索引只证明引用一致性，不能替代三轨实际执行或业务批准。
- 退出证据：V2 Live JSON/Markdown、三轨索引、NR 验收矩阵、残余限制及授权验收人的决定。
- 停止/升级：只读 verifier 失败不重跑 Agent、不 Apply、不改写历史；若修复需要改代码而改变 Revision，重新冻结并重做正式三轨。

## 4. 排序与证据矩阵

顺序为 S0 → S1/S2 → S3 → S4 → S6；S5 的隔离验证可随 S3 完成，真实恢复在需要时阻断 S6。工程可在依赖独立且已获委派时安排并行，但本计划不自动启动其他 Agent。

| 验收项 | 切片 | 最小证据 | 当前状态 |
|---|---|---|---|
| NR-01 制品/账号 | S1 | Bundle/Build Identity、clean-room、账号交付事实 | 实现机制已有；本阶段最终候选待执行 |
| NR-02 准备度/草稿 | S2/S3 | 同源 UI/API 正反证据 | v0.5.2 历史已验证；最终 SHA 待复测 |
| NR-03 环境/资格 | S0/S2 | 冻结身份与新鲜准备度 | 当前外部状态未知 |
| NR-04 真实 Candidate | S4 | Context、Attempt、四仓 Verification/Review/真实 PR | local-PR 历史通过；本阶段真实四仓待执行 |
| NR-05 Apply 完成 | S4 | Gate、四 Receipt、Manifest、healthy/completed | 本阶段未执行 |
| NR-06 恢复 | S5 | 原生故障与恢复链，标清运行模式 | 机制/历史回归存在；同候选待验证 |
| NR-07 正式验收 | S3/S6 | 三轨零容差原生报告与一致性索引 | 本阶段未执行，不复用历史报告追认 |
| NR-08 可用性/角色 | S2/S3 | 角色、桌面/移动、键盘及错误状态 | 历史局部验证；同候选待复测 |

## 5. 人的决策与执行约束

| 决策/未知项 | Owner | 最晚解决点 | 需交付的具体材料 |
|---|---|---|---|
| 本地 Alpha 范围、版本命名、评测/正式环境界限 | 项目负责人 | S0 结束 | 范围记录；版本可待正式候选确认，不臆造发布日期 |
| 四仓与知识源、权限、数据传输范围 | 管理员/项目负责人 | S2 外部操作前 | 受控资源清单、权限与已授权范围，秘密走安全渠道 |
| 真实运行预算及超时/失败升级规则 | 项目负责人/工程 | S4 前 | 当前冻结策略、预计调用范围；变更策略须独立评审 |
| 具体 Bundle 的业务 Gate/Apply | 获权管理员 | S4 决策点 | Candidate/Diff/Verification/Review/PR/Bundle 与风险说明 |
| 正式接受还是退回整改 | 项目负责人，主任务提供独立意见 | S6 | 完整 NR 矩阵、三轨索引与剩余限制 |

文档中 Owner 是职责分配建议，不冒充人员已接受任务。当前不提供工期或百分比承诺；外部资格检查和首条真实轨道完成后再据实际样本估算。工程只修复验收范围内已复现的问题，发现新需求先记录对 NR 的影响。未做或缺证据的工作保持待执行/未知，不由计划文本晋升为完成。
