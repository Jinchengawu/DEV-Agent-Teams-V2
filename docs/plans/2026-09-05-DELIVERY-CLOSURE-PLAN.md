# Agent-Team-OS 后续执行清单与产品差距

日期：2026-09-05
状态：首批实现已验证；P0-00/P0-01 完成，P0-02 基础子切片及确定性浏览器闭环通过。
审计基线：`codex/v051-live-readiness-hardening@7a4c706a88dec302f51da035b760a31c63b94e9e`。
本轮范围：本地 P0 代码、回归、契约和架构对账；真实业务 Gate 按原审批流程处理。
本文件是计划和证据导航，不拥有 Delivery、Release、Approval 或架构状态权威。

## 1. 当前判断与预期范围

最初 Backend MVP 的业务功能链已经基本覆盖；当前四仓团队与 Knowledge 扩展仍在真实集成收敛阶段。
接下来优先完成可恢复的真实交付与同 Revision 验收，再决定是否扩大产品范围。

这里的“最初预期”采用本仓库可追溯的 V0.1 承诺，不能代表建仓前未被记录的全部设想：

- 根提交 [dd148c8 的 README](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/blob/dd148c8/README.md)
  定义 Backend request → PM requirements → TaskContract → approval → Codex candidate →
  product verification → approval → atomic apply/reject。
- [ADR-0001](../architecture/ADR-0001-CLEAN-ROOM-V2.md) 限定一个内置 Backend Workspace、一个活动
  Delivery，不迁移旧聊天、RAG、多租户或前端执行；允许启动期使用显式 `codex-simulated-hermes`。
- [0f5368e 的 README](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/blob/0f5368e/README.md)
  进一步明确首版是标准库 Python 内置仓库，接入用户自己的仓库当时不在范围内。
- 四仓、Workcell、Tenant RAG 分别是
  [ADR-0012](../architecture/ADR-0012-MULTI-REPOSITORY-RELEASE-BUNDLE.md)、
  [ADR-0014](../architecture/ADR-0014-AGENT-WORKCELL-AUTHORITY.md)、
  [ADR-0017](../architecture/ADR-0017-FEISHU-TENANT-KNOWLEDGE.md) 等后续扩展。
- [Google Teamwork 观察](../research/GOOGLE-TEAMWORK-OBSERVATION.md) 是 Deferred Evaluation，
  不计入已承诺范围。生产 SaaS、任意工作区、自适应团队也不倒算为最初 MVP 欠账。

### 1.1 原始 MVP：按业务能力逐项核对

“存在回归”指当前仓库有相应可执行测试；本轮未重新运行测试。

| 原始能力 | 当前事实 | 剩余工作 |
|---|---|---|
| 1. Backend 需求转 Requirement/TaskContract | 契约、真实 Codex Adapter、确定性 API 回归均存在 | 最新 Revision 真实需求理解与输出质量重验 |
| 2. 计划审批后才执行，拒绝过期决定 | State/Version/Subject Hash 校验及正反回归存在 | 同 Revision 浏览器与 Live 联合验收 |
| 3. 隔离 Workspace 内执行 Codex | 真实 Adapter 与隔离 Worktree 已接线 | 选定真实运行配置重验，不沿用旧 Runtime 身份结论 |
| 4. 固定 Candidate/Diff/Evidence，拒绝越权和凭据 | 本地 Git、路径/秘密扫描、不可变 Ref 与 Hash 回归存在 | 当前端到端证据一致性重验 |
| 5. 产品机器验证 | 固定命令、退出码、Diff 重算与失败回归存在 | 原始 Python 范围内重验；其他技术栈是后扩适配 |
| 6. 二次审批与精确 Apply/Reject | CAS、回读、Receipt、拒绝不 Apply 的 API/Git 回归存在 | 最新 Revision 实际执行与拒绝路径重验 |
| 7. 持久化、重启和中断状态 | SQLite/Event、审批恢复、中断失败关闭路径存在 | 同 Revision 重启/恢复与公共接口回归 |
| 8. 整条真实用户闭环的版本验收 | 验收入口存在；本轮没有当前 Revision 完整通过证据 | 核心浏览器、Deterministic、Live 在同一 Revision 验收 |

前七项业务能力都有实现和回归路径；第八项尚未完成当前版本验收。这是条目覆盖，
不是 87.5% 的工时或产品完成率。当前存在更多模块，不等于最初承诺的 Runtime 组合已全部完成。
真实 Hermes 与 AgentScope Workcell Attempt Runtime 的资格和证据应单列，不把 Codex 替代路径计作它们已完成。

代码导航：
[需求与审批](../../src/agent_team_os/delivery.py)、
[隔离执行](../../src/agent_team_os/git_delivery.py)、
[Git 验证与 CAS](../../src/agent_team_os/git_sandbox.py)、
[API 回归](../../tests/test_delivery_api.py)、
[真实本地 Git 回归](../../tests/test_real_delivery.py)。

### 1.2 当前扩展产品：距离可交付还有哪些层

| 目标 | 当前成熟度 | 主要差距 | 对应任务 |
|---|---|---|---|
| 可配置项目、Pipeline、Team 和冻结配置 | 已实现，有确定性验证 | 新增验证政策的冻结/兼容、真实项目接入体验 | P0-02、P0-04 |
| 可观察 Main/Child、只读评审、机器验证 | 已实现，有局部真实执行 | 技术栈适配、Review 归属、最新修复的整链重验 | P0-02、P0-03、P0-06 |
| 四仓 Candidate/PR/Apply/Manifest | 确定性闭环；跨历史 Revision 有局部真实 Candidate/PR | 完整真实 Apply、回读和 Manifest 尚无成功样本 | P0-01、P0-05～07 |
| 部分发布失败后安全恢复 | 恢复机制和确定性测试存在 | Cancel/Lease/Drift 准入缺口，applying 取消竞态 | P0-01 |
| Feishu → Index → 冻结 Context → 引用 → 接纳 | 已实现，确定性 R2 闭环；历史 Readiness ready | 同 Revision 真实 R2 与失效/撤权边界验证 | P0-06、P0-07 |
| 操作者通过 UI 完成交付与恢复 | 页面与部分浏览器脚本存在 | 核心状态/合法动作核对，四仓 Live 浏览器验收入口 | P0-04、P0-05 |
| 主要版本可复现验收 | 检查器、Build Identity、报告模型存在 | 同 SHA 三类合格证据及清楚的交接索引 | P0-07 |
| 日常重复使用、净人工收益 | 未有足够证据 | 连续真实任务、与现有流程的人工时间/质量/费用对照 | P1-01、P1-02 |

不提供混合百分比：上述条目依赖和风险不同，正式版本验收是必须全部满足的门禁。
当前最有用的距离描述是：原始功能链基本齐全；扩展产品尚欠恢复治理、真实适配、
完整 Live 交付和同 Revision 验收；日常提效另需试点证明。

### 1.3 本地运行快照

采样：2026-09-05 13:31:59（Asia/Shanghai），只读 `live-v051` 评测库元数据。

- 当前最新 Delivery 停在 `awaiting_plan_decision`，没有 error，冻结产品 SHA 为 `7a4c706`。
  这是人工 Gate 等待，不是运行故障。
- `completed` Delivery、V2 Apply Attempt、Remote Apply Receipt、ReleaseManifestV2 均为 0。
- 6 个 verified Candidate 与 6 个 PR Receipt 分属多个旧 SHA，没有来自当前 HEAD 的完整成功链。
- 历史 Readiness 的 ready 仅说明当时具备启动资格；执行时仍需刷新。
- 当前 HEAD CI 已通过，但 CI 不包含完整四仓 Live/浏览器发布验收。
- 上一轮远端核查为 main=`805ad38`、当前分支独有四个修复提交；实施开始时须刷新，
  不依赖可能过期的本地 main/origin/main。

所有历史运行继续保留，不能回写 Snapshot 或把旧 Delivery 改成新 Revision 的证据。

## 2. 执行清单

整体顺序：

```text
核对基线
→ 保护发布恢复
→ 按仓验证与 Review 契约
→ 核心 UI/验收入口
→ 固定干净验收 SHA
→ 完整 Live R2 与隔离故障验证
→ 同 SHA 正式验收
→ 重复使用与价值对照
→ 决定后续扩展
```

任务 ID 只用于本文件跟踪。各任务完成必须填入其 Product SHA、运行/测试 ID 和证据位置；
不能把勾选本文件当作产品完成。最多并行两个开发切片，共享 Snapshot/Release 改动由一个负责人协调。

### P0：完成可验收的本地交付产品

- [x] **P0-00：核对工作基线与验收范围。**
  - 责任：实施主 Agent；产品范围和业务审批由项目 Owner 决定。
  - 检查远端 main、当前分支四个修复的集成状态、干净基线、ACWM Lock、
    已发布 Pipeline/Provider/Method/Workspace/Knowledge Revision，以及现有等待人工 Gate 的 Delivery。
  - 第一目标保持已承诺的四仓 R2 本地 Alpha，不顺带扩展新的 Runtime 或发布策略。
  - 输出：初始基线清单、范围清单和独立评测环境设计。这一步尚不冻结最终验收 SHA。
  - 用户已授权推送代码并推进全部 P0；Plan/Design/Release Gate 仍按产品权限和具体审批产物执行。

- [x] **P0-01：保护部分发布和执行中发布的恢复状态。**
  - 依赖：P0-00；在任何新的真实 Apply 前完成。
  - 禁止 `needs_attention` 经通用 Cancel 变成终态并释放 Lease；
    `release_drifted` 时拒绝新的 Delivery。覆盖 `applying` 与 Cancel 的竞争窗口。
  - 沿用原 Bundle 的 `resume-forward`，不新增 Force Push、自动 Rebase、回滚、补偿或“放弃恢复”。
  - 公共接口验收：隔离 Fake Remote 部分失败 → Cancel 被拒绝且状态/Receipt/Lease 不变 →
    新 Delivery 被拒绝 → 重启后约束仍在 → 同 Bundle 恢复成功并回读四仓 SHA →
    Manifest 激活、health=healthy 后才允许释放 Lease及后续交付。
  - 已知定位：[取消路径](../../src/agent_team_os/delivery.py)、
    [终态释放 Lease](../../src/agent_team_os/modules/projects/integration.py)、
    [项目准入](../../src/agent_team_os/modules/projects/application.py)。
  - 现有回归：[test_external_forward_release_v2.py](../../tests/test_external_forward_release_v2.py)。
    已补齐普通 Cancel、CAS、重启、错误终态、Lease 错配及恢复后再准入：26 + 7 + 10 项本地专项通过。

- [ ] **P0-02：让每个仓库按自身技术栈完成产品机器验证。**
  - 依赖：P0-00；与 P0-01 可分别审查后并行，Snapshot 变更统一集成。
  - 在一个真实四仓项目内竖向完成 Profile 配置/资格 → 发布冻结 → 执行 → 证据 → UI 可审查。
    Design 检查设计产物合同；Frontend 使用其实际 TypeScript/测试/构建命令；
    Backend 使用其真实测试命令；QA 验证约定的集成/E2E 结果，不统一套 Python unittest。
  - 详细方案先定义唯一 Profile 权威、适用 Workcell、命令、工具链版本、超时、
    非敏感环境描述、Snapshot 绑定和证据 Hash。环境中不冻结凭据值。
  - 运行命令由产品政策决定，Agent 不得重写命令来制造通过；零测试发现、命令缺失、
    超时、工具链或 Profile 漂移须有明确失败结果。
  - 旧 Snapshot 继续可读；不能回填新身份使历史运行获得新版本资格。新增必需资格缺失时，
    新运行 Fail Closed；具体 Schema/Migration 由独立详细 Plan 审查确定。
  - 已知定位：[当前统一 unittest 配置](../../src/agent_team_os/preview.py)。

- [ ] **P0-03：把 Review 归属从提示要求落实为可校验合同。**
  - 依赖：P0-00；与 P0-02 协调冻结合同和旧数据兼容。
  - 为当前 Workcell 的 Acceptance 归属定义明确字段或受控映射；Finding code、
    Acceptance ID 和 System Policy ID 不混为同一个任意字符串。
  - 只接受属于当前 Workcell 冻结 Acceptance 或显式冻结 System Policy 的阻断证据。
    未知/越界引用判 invalid review，保留问题证据并受控重试或人工处理；不能静默丢弃 Finding。
  - 程序校验归属不等于证明模型判断正确；还要用真实问题和无效阻断样本验证效果。
  - 同时复验最新 Diff 正文、Main synthesis 局部证据、只读 Overlay 和验证器字节码修复。
  - 公共接口验收覆盖真正缺陷仍被阻断、无归属 Review 不可通过、Candidate/Diff 错配不可通过、
    Main 不能覆盖机器失败，以及有界 Repair 的终态。
  - 已知定位：[Review 输出校验](../../src/agent_team_os/modules/workcells/stage_driver.py)。

- [ ] **P0-04：补齐核心操作体验和验收入口。**
  - 依赖：P0-01～03 的接口/合同稳定后完成。
  - 操作者能区分等待审批、执行失败、Review 无效、可恢复部分 Apply；
    能查看当前 Candidate/Diff、Verification、Review、PR 和应执行的合法命令。
  - Plan/Design/Release Gate 和 resume-forward 必须通过现有产品权限/命令路径；
    Board 不获得状态权威，UI 不能把运行中或 Drift 的交付拖成完成。
  - 先修影响核心闭环的状态/按钮/错误引导；普通布局、美化可后移。
  - 为四仓 Live 补合格的浏览器验收驱动或可重放的受控人工浏览器步骤；
    当前确定性四仓脚本不能直接冒充 Live 驱动。
  - 提供独立本地评测账号、URL、数据范围和 Runtime Identity。密码仅会话交付，
    不进入 Git、日志、Fixture、报告、截图或本文件。

- [ ] **P0-05：集成修复并固定最终验收 Revision。**
  - 依赖：P0-01～04。
  - 正常集成已审变更并固定干净 Product SHA、ACWM 依赖证明、Pipeline/Provider/Method/Policy 身份。
    不能用本地过期 main 作为已同步依据。
  - 运行适用的代码质量、公共接口回归、旧 V1 兼容、四仓恢复反例、
    Deterministic 四仓与 Knowledge R2 核心浏览器验证，保存明确的证据身份。
  - 修复若改变代码或合同，生成新 SHA 并重跑本次正式验收要求的检查。
  - 旧 `c12ba67d...` Delivery 永远属于 `7a4c706`；新 SHA 的验收须建立新的 Delivery，
    不复用或改写旧 Snapshot。

- [ ] **P0-06：完成真实四仓 R2 交付及隔离故障验证。**
  - 依赖：P0-05；运行前刷新 Readiness，凭据只用安全引用。
  - 首个正常样本：真实需求 → 真实冻结 Planning Provider → 人工 Plan Gate →
    Design Gate → 各仓 Candidate/Verification/Review → 四个 PR →
    人工 Release Gate → Forward-only Apply → 远端回读 → Manifest。
  - R2 验收同时覆盖真实 Feishu/Index/Ollama、7 个 Stage Context、
    5 个 Workcell（含 Artifact-only QA Preparation）、Citation 和结果接纳。
    基础四仓成功不能替代 Knowledge 验收。
  - 当前显式 Codex Planning 依冻结 Binding 验收；不得写成 Hermes 通过。
    若选 Hermes，必须以该 Provider 的真实资格和 Attempt 单独验收。
  - 在独立评测环境/Delivery 中覆盖超时、无效 Review、来源失效/撤权、
    发布中断与原 Bundle 恢复；外部操作仍走应有人工 Gate。
  - 故障试验以“正确阻断/恢复”的断言判定测试结果；保留失败运行，不通过删除失败案例制造绿报告。
  - 若遇新缺陷，回到修复、详细 Review（涉及边界时）和 P0-05，不放宽验收合同来赶进度。

- [ ] **P0-07：形成同 SHA 的正式交接证据。**
  - 依赖：P0-05、P0-06。
  - 同一干净 Product Revision 上具备核心用户浏览器、Deterministic 与 Live Release Gate。
    正式 Release Report 必须 `FAIL=0`、`WARN=0`、`skipped=0`。
  - V2 检查器只验证既有 completed Delivery；CLI exit 0 不自动合并浏览器/Deterministic 证据。
    用交接索引关联现有报告 Hash、SHA、Pipeline/Build Identity 和运行 ID，不新增事实权威。
  - 记录四仓远端 SHA、PR/Candidate/Verification/Review、Bundle、Apply Receipt、
    Manifest、health，以及独立评测账号的安全交付事实；账号密码不得落入报告。
  - 对账 README、产品文档和架构总览中与现行 Provider/Readiness/Live 证据矛盾的陈述。
    文档随实现变更集更新；若因此改变验收 SHA，最终证据必须重绑正确 Revision。
  - 通过后才称所选版本已验收；不能将 readiness、单次 CI 或部分 Workcell 成功替代这一结论。

### P1：证明能重复使用，并减少人工投入

- [ ] **P1-01：同产品 Revision 完成三个不同真实任务。**
  - 依赖：P0-07。
  - 三个业务任务可以有不同 Candidate SHA；固定的是产品 SHA 和约定运行条件。
  - 记录每次完整结果、重试、人工介入与失败原因，不能只展示成功 Attempt。
  - 三次连续成功是初步运行健康门槛，不构成生产 SLA。

- [ ] **P1-02：开展有时间上限的价值对照。**
  - 依赖：P0-07；P1-01 可作为同一试点的起始样本。
  - 项目 Owner 选约 10 个代表性真实任务，以“现有编码 Agent + Git/CI + 人工审查”为基线，
    冻结相同验收标准、可比模型/预算和起始仓库状态；使用独立分支，避免答案泄漏影响对照。
  - 首轮窗口最多两周，用于决策，不承诺两周完成整个产品。
  - 主要指标是每个合格交付的人工分钟，包括环境配置、排错、维护和审查；
    同时记录未完成任务及其已花费时间，避免只在成功样本内比较。
  - 护栏为交付缺陷、模型费用和总耗时。无法获取 Token/费用时标 unknown，
    不伪造零成本；数据缺口影响结论时补测。
  - 建议初筛目标：人工分钟约减少 30%，质量不下降、费用和等待时间在事先约定预算内。
    30% 是待采用的目标，不是现有测量结果或统计显著性结论。
  - 达标且愿意重复使用：聚焦该任务类型继续产品化；未达标：缩减流程/暂停扩张，
    保留可复用的证据、审批和恢复能力。达到时间上限仍缺闭环时，不靠增加平台范围延长试验。

- [ ] **P1-03：依据测量结果消除最昂贵的操作和调用。**
  - 依赖：P1-02 的瓶颈证据。
  - 优先处理重复配置、难定位失败、无增益的 Main planning 调用等；
    不预设一定删除某个角色或直接切换单仓模型。
  - 新增单仓模式、改变 DelegationPlan/Attempt 合同或省略模型阶段时，
    重新做详细 Architecture Review 和兼容分析。之后的新版本不能沿用旧 SHA 发布证据。

### P2：在明确需求后补齐架构愿景

- [ ] **P2-01：AgentScope Attempt Runtime Adapter。**
  - 沿用已接受 ADR-0014；先做独立实施 Plan、Adapter 资格、身份/取消/超时/重启测试，
    再收集真实 Live 证据。不得创建 Hidden Child 或第二套 Workcell/跨 Stage 权威。
  - 原 Codex 直连路径的成功不计作本项完成。
- [ ] **P2-02：真实 Hermes Planning Provider 分支。**
  - 仅在目标 Pipeline 选择该 Provider 时完成相应资格、Binding、Attempt 与 Live 验收；
    不给已冻结 Codex 的路径追加替代身份。
- [ ] **P2-03：按试点决定扩展。**
  - 暂缓 v0.6 深层派生、跨 Delivery 并发、非 Git Workspace、多租户、
    自适应角色和 Provider-native Merge；这些不计入 P0 的验收欠账。
  - 任何改变四仓、Apply 或 Runtime 权威的提案独立审查，不由此路线图预先接受。

## 3. 验收入口

以下为验收入口导航；本轮实际运行结果见第 7 节。运行时使用独立数据目录、正确 Feature Flag 和安全的会话账号。

| 用途 | 入口 | 限制 |
|---|---|---|
| 四仓确定性浏览器 | `scripts/browser_workcell_e2e.py --url … --data-dir … --screenshot …` | 先启动 `agent_team_os.gate_app:app`；Agent/PR 为确定性 |
| Knowledge A/B/C 与 R2 | `scripts/browser_feishu_knowledge_e2e.py --url … --gate-c --data-dir … --state … --screenshot …` | 同一 gate_app；三个 Feature Flag 按依赖启用；仍是确定性 |
| Live Readiness | `agent-team-os knowledge-live-readiness --project-id <id>` | 只证明启动资格，`ready/not_run` 不等于通过 |
| Live V2 验收 | `agent-team-os knowledge-live-gate --project-id <id> --delivery-id <completed-id>` | 只验证已完成运行，不启动 Agent、不 Apply、不代替审批 |
| 旧 V1 回归 | `agent-team-os gate`、`gate --live`、`release` | 保留的单仓门禁，不能替代四仓 V2 |

检查器依据：[Release Acceptance 实现](../../src/agent_team_os/modules/releases/acceptance_application.py)、
[报告模型](../../src/agent_team_os/modules/releases/acceptance_domain.py)。
初次只读评估没有运行这些命令；后续实施结果见第 7 节，旧 Live Plan Gate 未被处理。

## 4. Architecture Review 记录

审查对象是初次路线图审查计划文档；各功能切片的详细 Plan 必须另行完成
Draft → Review → Revise → Final → Implementation → Reconciliation。

### 第一轮：Draft Review

- Architecture Impact：`None`（仅文档）。
- Findings：初始基线与最终 SHA 未充分分离；恢复修复表述可能歧义；Profile/Review 的权威、
  Snapshot 与历史兼容需单列；影响核心操作的 UI 应上移；四仓与 R2 验收边界需明确。
- Required Revisions：明确禁止 Cancel 释放恢复 Lease；覆盖 applying 竞态；最终 SHA 后置；
  Profile/Review 先独立详细审查；关键 UI/Live 浏览器入口上移；保留独立故障与完整证据；
  固定产品 SHA 的重复任务不冒充 SLA；Codex/Hermes 身份分开。
- ADR Required：初次路线图审查不需要；未来切片按真实影响决定。
- Architecture Document Delta：无；不将路线图写成已接受架构变更。
- Outcome：`Revise`。

### 第二轮：修订复审

- Architecture Impact：`None`（仅初次路线图审查文档）。
- Findings：产品权威和 Module/Port/Adapter 依赖保持；Profile/Review 唯一事实源、版本、
  Snapshot 兼容及资格需后续详细审查；取消/Drift/Lease 与 applying 竞态已纳入；
  Workspace/凭据隔离不变；V1/V2 历史保留；未知归属 Finding 和故障证据不丢弃；
  最终验收 SHA 后置，浏览器/Deterministic/Live 证据分别关联；原始 MVP 与扩展范围分开。
- Required Revisions：无。已提交修订均纳入本文件；v0.5.1 正式验收必须覆盖完整 R2，
  四仓 Live 浏览器入口仍是待补能力，入口可用不等于闭环通过。
- ADR Required：`None`（仅初次路线图审查文档）；各功能切片按实际影响独立决定。
- Architecture Document Delta：`None`；初次路线图审查不修改总览或既有 ADR，不晋升任何架构状态。
- Outcome：`Approved`（独立审查 Agent 复审通过，仅批准计划文档定稿）。

文档复审不替代具体功能的详细 Plan，也不授权代码、数据库、分支、Live 执行或业务 Gate 批准。

### 未来切片的架构门禁

| 切片 | 必查事项 | ADR 条件 |
|---|---|---|
| P0-01 | Release/Delivery/Project 准入、取消竞态、持久状态与 Lease、重启和原 Bundle 恢复 | 恢复 ADR-0015 既定不变量可不新增；若改变取消/补偿/恢复语义，必须修订 ADR |
| P0-02 | Profile 唯一权威、执行信任、Snapshot 一致性、命令/工具链资格、Legacy 可读性 | 新持久化权威、一致性或安全边界变化须新增/修订 ADR |
| P0-03 | Finding 归属、Acceptance/System Policy 来源、Review 接纳、历史兼容 | 新 Policy 权威或 Gate/Review 语义变化须新增/修订 ADR |
| P0-04 | UI 只表达合法命令，不拥有状态；权限以服务端为准 | 局部 UI 无边界变化不强制 ADR |
| P2 | 不复制 ACWM Contract；产品先创建 Attempt；Adapter 不产生 Hidden Child | 对账已接受 ADR；新增外部集成/信任策略须重新审查 |

正式接受的新架构按规则同步写入总览的 `Accepted/Not Implemented`；
实现和相应验证完成后才晋升。当前路线图不提前改变任何一项的架构状态。

## 5. 计划完成后的交接检查

- [ ] 每个声称完成的切片都有公共接口/运行证据，不能仅链接实现代码。
- [ ] 正式版本三类门禁绑定同一干净 Product Revision，Release Report 三项零值。
- [ ] 配置、测试、局部 Live、完整 Live、重复使用和商业收益分开描述。
- [ ] 已知缺口、失败运行和后移能力保留，账号密码和业务秘密不进入文档。
- [ ] Owner 根据真实任务收益选择继续、缩减或暂停下一阶段；不以代码量代替使用价值。

## 6. 首批实施审查（2026-09-05）

用户已明确要求开始推进以上计划。P0-00 复核：当前 HEAD/远端 hardening 均为
`7a4c706`；远端 main=`805ad38`，当前目录没有承载热重载服务。已有 Live 运行冻结在旧 SHA，
保留其 Gate 与历史证据，本轮测试使用临时数据库和独立本地 Remote。

### P0-01：Draft → Review/Revise → Final Plan

- Architecture Impact: Critical
- Findings: Cancel 与 Apply 存在线程竞争；终态即释放 Lease；远端成功后的分散持久化可暴露错误终态。
- Required Revisions: Cancel/Candidate Reject 以 CAS 进入非终态 cancelling，清理不取消自身；
  清理失败与重启保留既定意图/Lease。Apply 用 CAS 裁决，输方无副作用。
  Release 通过只读查询提供恢复 owner，保护准入、Lease 回收与重复任务对账；多个 owner 失败关闭。
  组合根验证同一 SQLite 文件；初始 Lease 事务内复查。Manifest/Attempt/Health/Delivery/Event/Lease
  原子提交；异常先回读完整提交，不覆盖已提交胜者。
- ADR Required: Yes，修订 ADR-0015。
- Architecture Document Delta: ARCH-20260905-01；先登记 Accepted/Not Implemented。
- Outcome: Approved（第二轮复审后以上约束为 Final Plan）。
- 验证：公共 API、真实 SQLite 并发、取消清理失败恢复、原子提交回滚/提交后异常、本地 FakeRemote。

### P0-02：Draft → Review/Revise → Final Plan

- Architecture Impact: Cross-boundary
- Findings: 现有 Git 资格不是机器验证资格；四仓全局 unittest 命令不能表达实际技术栈。
- Required Revisions: 产品预置 Profile，Workcell Governance 拥有资格；冻结到 Delivery/Workcell，
  Writer 复核工具身份，旧空字段兼容原 hash；拒绝未知/篡改 Profile 与零测试成功。
  首批只发布可靠验证的 Python unittest / Node native test。pnpm 方案待证明零测试拒绝能力后开放。
- ADR Required: Yes，修订 ADR-0014、ADR-0019。
- Architecture Document Delta: ARCH-20260905-02；先登记 Accepted/Not Implemented。
- Outcome: Approved（限定上述首批范围）。
- 验证：权限与配置 CAS、实际工具资格、快照兼容/篡改、真实临时 Python/Node 命令、Console。

实现和测试通过只更新对应任务证据，不自动视为 Live 或正式 Release 验收。

## 7. 当前进度与剩余边界

- P0-00：已完成。本地/远端基线和运行服务隔离已复核。
- P0-01：已完成本地实现与 43 项专项验证。新增取消中、CAS、恢复 Owner 保护和原子发布完成事务。
- P0-02：已完成首批 Python/Node 基础实现、回归与架构对账；
  原任务要求的实际 Frontend TypeScript/build、Design 合同、QA E2E 与真实四仓适配仍未完成，不能勾整项。
- P0-03：未实施；下一步冻结 Acceptance/System Policy 归属并校验 Review Finding 引用。
- P0-04：随本轮补了取消状态和 Profile 配置 UI；完整 Live 浏览器驱动仍未完成。
- P0-05～07：未冻结最终 Product Revision；没有新增 Live 运行或业务 Gate 批准。

改动当前位于本地工作区，未提交/推送；旧 Live Delivery 及远端 main 保持原业务状态。
以上完成状态只覆盖本地实现和已列测试，不代表正式 Release 验收。

### 浏览器确定性 Adapter 兼容修复审查

- Architecture Impact: None
- Findings: 浏览器使用的公共 Deterministic Reviewer 缺少既有契约要求的 Candidate/Diff SHA；
  单测中的自定义 Fake 已更新，公共 Adapter 尚未同步。浏览器失败准确暴露该兼容缺口。
- Required Revisions: 从冻结 Candidate Review Evidence 读取并返回两 SHA；证据缺失失败，不放宽产品校验。
- ADR Required: No
- Architecture Document Delta: None
- Outcome: Approved

该修复归入 P0-04 的确定性验收入口维护，不代表 P0-03 Review Acceptance 归属合同已完成。

### 本轮验证结果（本地工作区，非正式 Release Report）

- 最终全量 Pytest：409 passed、1 skipped；跳过项为默认未开启的真实 Codex 集成测试。
- P0-01：26 项取消/CAS + 7 项 Release + 10 项恢复 Guard 通过。
- P0-02：25 项 Profile/Workspace 配置/Snapshot/Stage/四仓组合通过；两个安全阻断修复后独立复审 Approved。
- 公共 Deterministic Reviewer：4 项直接 Adapter 回归通过；保留现有产品的两 SHA 校验。
- Console：27 个测试文件、83 项通过；TypeScript、Vite build 与 OpenAPI 精确重生成一致性通过。
- Ruff 与 175 个源文件 Mypy 通过，Python sdist/wheel 离线构建通过。
- 确定性浏览器：独立数据目录、临时本机端口、随机评测账号；5 个 WorkcellRun succeeded，
  Delivery `7ce0a8a7-ffd7-423d-af2a-d075b89b04d7` completed，error=null，Project Lease=0；
  四个本地 Remote 经 Forward-only Apply 和读回验证，Manifest
  `a6cf43c084495a5d95a2c8a51f3477ee58290cc478cb18fa24e22105e43daa82` 已形成。
- 浏览器截图保存在本地忽略目录：
  [四仓完成状态](../../.agent-team-os/reports/p0-20260905/workcell-browser.png)。临时服务已关闭。

浏览器使用 `deterministic-model-boundary`、本地 Bare Git 和无 Knowledge 的四仓 Pipeline，
不是 R2 Knowledge Live，也不是正式 Live Release Gate；旧 `live-v051` 运行没有被改写。

## 8. 持续执行目标

用户最新授权：推送代码并持续推进，直到 P0-00～P0-07 全部完成。
本轮先推送已验证首批，随后补齐真实四仓技术栈、Review 归属、UI 和同 Revision 三类验收。
405/409 项本地测试、首批提交或 CI 通过都不是目标完成条件。正式验收必须逐项证明原 P0 范围。
