# 下一阶段产品文档：四仓 R2 正式交付准备与验收

## 1. 文档状态与产品决策

2026-09-22 执行更新：用户已明确委派 software_delivery_engineer 按本文与计划落地，随后由 product_manager 产品验收；当前为 `Implementing`。下列原始评审状态保留编写时边界，实施授权不等于具体业务 Gate/Apply 决策。

- 状态：`In review`；本次完成产品定义与执行计划文档，尚未据此启动实施或真实交付。
- 日期：2026-09-22。
- 产品负责人：product_manager；独立验收负责人：主任务；执行负责人：software_delivery_engineer（职责建议，启动以实际委派为准）。
- 审阅人：项目负责人、架构审查人、交付管理员。
- 检查基线：`main@5395a4bc1a2ed5960c774a7f12bb417f896053a5`；文档编写前本地工作树干净。本轮未重跑 CI、浏览器或 Live。
- 关联计划：[下一阶段交付计划](NEXT-STAGE-DELIVERY-PLAN.md)。
- 版本号：待产品负责人决定；本文不自动命名为 v0.5.3 或 v0.6。

目标是让交付负责人在受控本地 Alpha 环境中，取得可独立安装评测的制品，并在明确授权的四个评测仓库上完成真实交付，最后获得同 Revision、可复核、零容差的正式交接证据。交付标准为现有 `four-repo-r2-alpha`，不是公网生产服务或生产 SLA。

当前核心交互已有实现和历史本地验证，下一阶段优先解决“能否复现、能否真实完成、能否独立验收”。不以新增页面数量衡量进度，也不把此前对话中的完成百分比当作测量基线。阶段完成度按本文件的验收项逐条判定。

## 2. 事实、解释、假设、未知与人的决策

| 类型 | 结论或问题 | 来源、边界与验证责任 |
|---|---|---|
| 事实：当前本地基线 | HEAD 为上述 SHA；读取前工作树无修改 | 本轮本地 Git 只读检查；不等于本轮远程 CI 或运行实例身份验证 |
| 事实：实现记录 | Project onboarding、统一 Setup Readiness、服务端启动阻断、运行室与 Evidence 路径已落地 | [架构总览 §11–13](../architecture/ARCHITECTURE.md)、[v0.5.2 验收 §5](V0.5.2-INTERACTION-CLOSURE-ACCEPTANCE.md)；不是本轮行为复测 |
| 事实：历史已验证 | v0.5.2 Deterministic 与真实 Codex local-PR 路径达到 Candidate/Evidence/onboarding ready；真实路径保留合法 Repair 历史 | [v0.5.2 验收 §2 Fact 25、§7](V0.5.2-INTERACTION-CLOSURE-ACCEPTANCE.md)；使用当时 dirty 工作树和本地 PR surface，未在当前干净 SHA 重验 |
| 事实：验收契约 | 正式交接要求三条原生证据轨道、同干净 Product/ACWM Revision、各自 fail/warn/skipped 均为 0 | [交接 Runbook](../runbooks/DELIVERY-HANDOFF-EVIDENCE.md)；本文不降低阈值 |
| 事实：顺序约束 | Release Acceptance V2 检查已完成 Delivery、四份 Receipt、active Manifest 和 healthy | [Verifier](../../src/agent_team_os/modules/releases/acceptance_application.py)、[ADR-0019](../architecture/ADR-0019-RELEASE-ACCEPTANCE-V2.md)；该报告在真实 Apply 之后生成 |
| 解释 | 当前主要缺口是跨环境复现、真实外部链路和完整验收证据，不宜立即扩展新 Runtime 或新产品域 | 基于上述证据的产品优先级判断；如当前 SHA 复测失败，应先修复实际失败 |
| 假设 | 本阶段仍以可信本机、四个专用评测仓库和受批准知识源为交付范围 | 项目负责人在真实执行前确认；若改为生产仓库或公网部署，需要另定范围和风险标准 |
| 未知 | 当前 Feishu/Ollama、四仓权限、Verification Qualification、模型/Method 资格与运行实例身份是否满足冻结合同 | 交付管理员与工程负责人在阶段 A 提供新鲜、脱敏的准备度证据 |
| 未知 | 连续运行时延、失败率和恢复稳定性 | 工程记录所有尝试的样本数、耗时、失败码与恢复结果；当前没有可靠 P95/P99 或成功率基线 |
| 人的决策 | 本轮用户要求整理下一阶段产品文档 | 只授权本轮文档工作；历史上下文传输许可继续按原范围保留，不扩展为业务 Gate 批准或 Apply 许可 |
| 待决策 | 真实执行的项目、四仓、知识范围、审批人、预算及外写范围 | 在阶段 B 前形成具体决策记录；已有明确授权按原范围复用，缺项不能由文档代签 |

历史总纲仍包含旧版本、旧分支、旧取消行为和旧统一 Python 验证方案描述；历史 v0.5.2 报告 §6 的“未观察真实闭环”也与其后续 Fact 25/§7 不一致。它们保留历史来源价值，当前行为以代码、冻结合同和新验收事实为准。下一阶段只对交付相关矛盾做有来源的对账，不改写历史结果。[事实优先级](../architecture/ARCHITECTURE.md#11-事实优先级)

### 历史问题台账（含用户提供的图 1）

| 历史问题 | 后续证据边界 | 下一阶段责任与解除证据 |
|---|---|---|
| 四仓 Verification Qualification 失效 | 图 1 记录 runner/工具/依赖身份变化后旧资格被拒；这是预期 fail-closed，不能直接解释为缺陷已修复或仍故障 | S2 工程核对当前身份；需要重新资格化时登记状态写入范围，以四仓新 Qualification 与冻结 Snapshot 解除 |
| Feishu 权限证据不新鲜 | 图 1 指出恢复需要实际 Feishu 探测及写入；当前没有本轮新鲜度探测结论 | S2 管理员提供 Source 级权限、新鲜度与索引/批准范围证据；旧 Binding 缓存不能替代 |
| Planning 120 秒超时 | [v0.5.2 验收 Fact 9–25](V0.5.2-INTERACTION-CLOSURE-ACCEPTANCE.md) 记录后续成功 Planning 与完整 local-PR 路径；不能据单次成功推导稳定 SLA，也不能继续把旧失败当当前必现 blocker | S4 工程按冻结模型/effort/timeout 跑正式轨道，保留全部 Attempt 时长/失败码；若再现先定位，再评审策略变更 |
| Python 3.11.1 被误判环境错误的风险 | 它满足仓库 `>=3.11,<3.13` 版本范围；版本合法与 sqlite-vec 扩展可用、工具资格一致是不同检查 | S2 分别证明版本、SQLite 能力与实际工具资格；不仅凭版本号要求升级 |
| 浏览器/三轨报告/真正交付尚缺 | 历史 local-PR 交互已通过；同干净候选正式三轨仍需重新形成 | S3–S6 按 NR 矩阵解除；不以构建或 CI 代替 |

截图是历史问题来源，不是本轮命令授权；本表以仓库后续记录限定其当前解释。

## 3. 用户与范围

首要用户是需要判断“是否能启动、在哪里审批、交付是否完成”的交付负责人；管理员负责运行环境、凭据引用、资源治理和高权限决策；独立评测者负责安装、操作和证据回读。Agent 只执行冻结职责，不拥有验收或发布权威。

### 必须完成

1. 同 Revision 的可校验制品、干净安装和独立评测入口。
2. 四仓 R2 执行依赖的可解释准备度与已冻结资格。
3. 受授权的真实四仓 Delivery，完整保留 Knowledge、AgentAttempt、Candidate、Review、PR 与 Apply 证据。
4. 同 Revision 的原生 Browser、Deterministic、V2 Live 报告及交接索引。
5. 失败、权限、漂移和恢复路径的证据；只修复影响这些验收项的实际缺陷。

### 本阶段不承诺

- AgentScope Workcell Live Adapter、新 Evaluation Console、二级 Child、跨 Delivery 并行 Lease、Provider-native PR Merge。
- 公网部署、多租户、生产 SLA、通用 RAG、长期共享 Agent Memory。
- 以改模型、放宽 timeout、跳过验证、隐藏失败或替换历史报告实现通过。
- 建立新的审批、状态、报告或 Runtime 权威；优先使用现有 CLI、公共 API、浏览器和原生报告。

## 4. 用户流程与可观察要求

| ID | 用户场景与要求 | 边界/反例 | 验收证据 |
|---|---|---|---|
| NR-01 | 独立评测者拿到 Bundle 后，校验 Manifest，在独立 Data Root 安装并登录；无需开发 checkout 提供运行资源 | 缺资源、篡改、错误 Product Root 必须失败关闭；不覆盖已有数据 | 制品 Hash、干净 Build Identity、安装启动记录、独立账号安全交付记录 |
| NR-02 | 交付负责人进入准备中心，看到每项阻塞原因、修复入口、权限和下一动作；修复刷新后草稿仍在 | 页面请求失败或资格漂移不得乐观放行；启动 API 同源阻断 | 公开 UI/API 的正反证据、配置/Qualification Hash、脱敏 readiness receipt |
| NR-03 | 管理员核对四个不同仓库、冻结 Provider/Method/Verification 与七个 Knowledge Context；缺项时在运行前定位 | Python 满足版本范围不等于 sqlite-vec 可用；Codex Planning 按 Binding 验，不强制虚构 Hermes 依赖；旧资格失效可为正确行为 | 当前工具与锁身份、新鲜 Source/Index/权限检查、四仓治理证明 |
| NR-04 | 负责人创建 R2 目标，经已授权 Plan/Design 决策，观察全部五个最终 Workcell 与四个 Candidate | QA Preparation 是 Artifact-only；历史失败 Repair 可保留，但必需 Stage 最终轮必须成功；local PR surface 不替代真实 PR | Snapshot、Context/Citation、可观察 Attempt、Verification/Review 与 GitHub PR Receipt |
| NR-05 | 管理员检查不可变 ReleaseBundleV2 后作具体发布决策；Apply 前核对 Base，逐仓非 Force 推进并回读 | 候选 SHA、Diff、Review、PR 或 Bundle 变化使旧批准无效；不允许靠 GitHub Merge 代替产品 Apply | Gate Subject/CAS、四份 RemoteApplyReceipt、远端回读、active Manifest、healthy 与 completed |
| NR-06 | 出现部分 Apply 或回读丢失时，负责人看到 needs_attention 和恢复责任；获权管理员按原 Bundle 恢复 | 不回滚成功仓、不 Force Push；已推送且精确同 Candidate 可恢复 Receipt；第三个 SHA 必须阻断；Lease 不提前释放 | 隔离故障验证的原生记录、恢复前后 SHA、Receipt、Manifest、Health/Lease；不得在未经许可的真实仓注入故障 |
| NR-07 | 独立验收者在 completed 后运行只读 V2 verifier，并关联三轨原生报告 | Report 不运行 Agent、不批准 Gate、不执行 Apply；缺轨、错 SHA/Build、篡改或非零 FAIL/WARN/skipped 均不能通过 | 三轨原生报告及其原生 Hash、文件 Hash、handoff-index、独立审查结论 |
| NR-08 | Viewer/Editor/Administrator 沿同一主路径看到自身下一动作；错误与运行状态可读、可恢复 | Viewer 无写入口且服务端拒绝越权；键盘焦点可进入/退出对话框，移动导航不阻断主任务；空/加载/错状态保留上下文 | 核心桌面/移动视口、键盘及角色走查，关联当前 Revision；发现缺陷按切片修复 |

用户主路径保持“登录 → 准备中心 → 项目目标 → 计划/设计决策 → 执行 → 候选与证据 → 经授权发布 → 发布证据 → 独立验收”。中文说明优先，SHA、Provider、CAS 等详情按需展开；不新增一个必须学习的配置入口。

## 5. 三阶段退出合同与 Gate 顺序

| 阶段 | 可以说明的结果 | 退出条件 | 不能说明的结果 |
|---|---|---|---|
| A：可评测候选 | 制品可安装；准备度和本地核心闭环可复核 | NR-01/02/03/08 对应范围完成；Browser/Deterministic 原生证据按计划生成；真实执行范围可审阅 | 不代表 Live Delivery completed 或正式交付 |
| B：真实交付执行 | 经明确范围授权，完成真实 Candidate、审批、Apply 与回读 | NR-04/05；四仓 Receipt、Manifest、healthy、completed 一致；恢复要求 NR-06 有证据 | 不代表事后只读正式验收已经通过 |
| C：正式交接验收 | 同 Revision 四仓 R2 Alpha 证据完整 | NR-07；Browser/Deterministic/Live 三轨均零容差，全部必需项无未解决阻断，由授权验收人作结论 | 不代表生产安全审计、SLA 或真实用户价值验证 |

必须区分业务人工 Gate 与验收 runner。`knowledge-live-gate` 是对已完成 Delivery 的只读校验，可写报告文件但不改变业务状态。它要求 Apply Receipt 和 Manifest，因而不能被列为**该次** Apply 的前置条件。实际顺序是：准备检查及执行前应有的合同证据 → 经授权的业务 Gate/真实 Apply → completed → V2 Live Report → 三轨索引与正式验收。

如果现有执行路径的前置校验要求一个尚不可合法取得的报告，应记录契约冲突并提交架构复审，不能关闭校验或伪造报告。本轮没有批准任何业务 Gate 或执行 Apply。

## 6. 数据、测量与发布约束

验收指标是可判定的证据完整性：同 Product/ACWM Revision、匹配 Build Identity、七个 Context、五个必需 Workcell、四组 Candidate/PR/Verification/Review、四份 Receipt、唯一 active Manifest、healthy，以及每轨 `FAIL=0 / WARN=0 / skipped=0`。具体字段和有效性以原生报告契约为准，不新增影子指标。

执行时保留每条轨道的实际 Project/Delivery/Pipeline、开始结束时间、Attempt 数、失败码和报告 Hash。三轨可以是不同 Delivery；不得拼接成一条虚构成功运行。性能数据先形成基线，不对单次通过推导稳定成功率或 P95/P99。

制品与运行数据分离；报告写入忽略目录或受控交接位置。正式报告不得包含密码、Token、Credential Reference、仓库 URI、知识正文或模型原始输出。独立评测账号只登记安全交付事实，密码不进入 Git、日志、Fixture、截图或报告。临时证据在交接前必须完成可访问性与 Hash 检查；失效路径记为证据缺失。

发布仍使用已有 Forward-only 策略；部分成功只按原 Bundle 恢复。新增代码修复、依赖锁或文档提交会改变候选 Revision；最终冻结后须用最终 SHA 生成三轨，不能沿用旧 SHA 结果追认。交付版本号、对外发布渠道、真实环境及操作预算由项目负责人决定。

## 7. 下一次验收事件

下一事件为产品/工程交接评审：主任务逐条审阅 NR-01 至 NR-08 和关联计划，确认真实交付范围、责任人与未决依赖。随后由获委派的工程负责人完成阶段 A 的具体候选和证据包。阶段 B 的真实外写与业务决策以具体对象及已存在的有效授权为准；文档审阅通过本身不构成执行授权。最终产品验收由主任务独立汇总，并交授权负责人决策。
