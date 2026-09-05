# ADR-0014：Agent Workcell 权威关系与隔离工作区

状态：已接受
日期：2026-08-31
修订：2026-09-04

## 背景

设计、前端、后端和 QA 是四个可独立交付的软件工作单元。它们拥有各自的
Git Repository，不是同一 Git Workspace 中的四个 Agent 角色。仅用聊天历史或
Agent 内部派生来协作，会丢失调度、取消、超时、工作区权限、验证和评审证据。

## 决策

Agent-Team-OS 引入 `Agent Workcell` 作为 Stage 内的产品级执行单元，并按下表划分
唯一权威：

| 领域事实 | 唯一权威 |
|---|---|
| Stage、DAG、Gate、Loop、Artifact Contract 语义 | ACWM |
| 不可变发布 Revision、Provider/Workcell/Release Binding | Agent-Team-OS Published Pipeline Revision |
| Workcell 身份、拓扑、Workspace 要求、Delegation 上限 | TeamTemplate Revision |
| Team 选择和真实仓库绑定 | Project Governance |
| Main/Child 组合、调度、取消、超时、生命周期和结果合成 | Workcell Execution Module |
| 单个 AgentAttempt 内的 Stage-local Session、消息和 Runtime Transport | AgentScope |
| Candidate 验证、Approval、Apply、Manifest | Release Module |
| BMAD/TEA 版本、内容和入口 | Agent Deployment Extension Snapshot |

`TeamTemplateRevision` 不包含 Stage 顺序、Provider、Deployment、Runtime Instance、凭据、
真实仓库或 Release Participant。Pipeline 发布时冻结 `workcell_stage_map`、
`release_contract_snapshot` 和所有 `main`/`delegate_*` 的 `ResolvedProviderBinding`。Delivery
启动时将 Team、Pipeline、Provider、Workspace 和 Method Pack 编译为不可变
`DeliveryExecutionSnapshot`。

## 执行不变量

1. 一个 ACWM Stage Attempt 对应一个 `WorkcellRun`。
2. `AgentRun` 表示逻辑 Main/Child；`AgentAttempt` 表示一次真实 Provider 调用。
3. Main planning 和 synthesis 是同一 Main Run 下的两个 Attempt。
4. Child 深度固定为 1；每个 Main 最多三个 Child，最多两个并发，最多一个
   `workspace_write` Child。
5. Writer 使用本 Workcell 的隔离可写 Worktree；Reviewer 只读同仓已冻结 Candidate。
6. 不挂载其他 Workcell Repository，也不用 Codex `--add-dir` 伪装只读跨仓输入。
7. 跨 Workcell 仅传递已校验的内容寻址 `ArtifactEnvelope`；Git Candidate Workcell 必须同时输出
   `workspace-candidate-v2` 元数据和 `workspace-candidate-diff-v1` Diff 正文。Diff 正文必须绑定冻结
   `diff_sha256`、通过凭据扫描并受总 Attachment 大小限制；下游从 Artifact Store 读取，不挂载上游仓库。
   不传 Session、Memory 或原始聊天历史。
8. Writer 的机器验证通过后 Reviewer 才能启动；Main synthesis 必须获得本 Workcell 的 Child Artifact、
   Machine Verification 和 Review Artifact 冻结证据，不能从缺失的局部事实推断成功；Main 不能覆盖机器失败或
   Blocking Review。
9. Repair 由 ACWM bounded Loop 创建新 `WorkcellRun`，不在 Child 中递归派生。
10. 取消 Workcell 会取消其 Delivery 执行任务，传播到未完成 Child，并终止正在运行的
    Codex 子进程。重启时未完成 Codex Attempt 标记为 `interrupted`，不伪装续跑成功。
11. 每个 Main/Child 必须先由产品创建 `AgentRun` 与 `AgentAttempt`，Runtime Adapter 才能执行；
    AgentScope 不得产生 `Hidden Child`（隐藏派生），即在产品不可见的位置派生 Child 或新的运行身份。
12. Candidate 冻结前必须拒绝 `_bmad`、`.agents/skills`、`__pycache__`、`*.pyc`、`*.pyo` 等方法安装产物
    或运行时生成物；凭据检查必须覆盖完整 Diff，包括删除行，防止敏感内容进入 Artifact Bus。
13. Product Machine Verification 子进程只能继承运行测试所需的系统环境白名单，不得继承 Feishu、
    GitHub 或其他 Token/Secret/Password；Python 验证固定设置 `PYTHONDONTWRITEBYTECODE=1`，避免验证器
    自身在已冻结 Candidate Worktree 中生成缓存文件并改变观察环境。

## AgentScope Attempt Runtime 边界

2026-09-02 的架构审查明确：可观察的 Workcell Composition 与生命周期属于 Workcell Execution
Module；AgentScope 只拥有一个产品已创建 `AgentAttempt` 内的 Stage-local Session、消息和 Runtime
Transport。这样既保留 AgentScope 的运行时能力，又避免其内部 Team/Child 绕过产品的并发、权限、
取消、证据和审计约束。

当前 Preview 仍由产品直接调度 Codex Main/Child Attempt；真正的 AgentScope Attempt Runtime Adapter
尚未接线，按 `Accepted/Not Implemented` 管理。在接线前，不得把 Workflow Manifest 或依赖包存在
表述为 AgentScope Live 证据。

## Method Pack Overlay

BMAD/TEA 是方法扩展，不是新的 Pipeline 或 Git 权威。产品只从锁定的 npm 归档构建
内容寻址的 Runtime Overlay；不执行安装脚本。Runtime Overlay 分为两层：

1. 只读、临时 `CODEX_HOME` 负责 Method Entry 发现，并显式关闭 Codex
   `multi_agent`，避免 Method 在产品可观测 Main/Child 树之外派生运行实体。
2. 仅在已登记 `AgentAttempt` 存活期内，产品可在该 Attempt 已授权的当前
   Workspace 中装配临时 `_bmad` Project Support Overlay。其脚本来自同一
   Content/Qualification Hash 的只读 Snapshot，只包含运行配置、渲染目录和方法所需脚本引用。

Project Support Overlay 必须在子进程启动前装配，通过 Attempt 专用 Git
`core.excludesFile` 对子进程隐藏，并在 Candidate 冻结前删除。Candidate Policy 仍独立
拒绝 `_bmad/**`，不将 `.agents/skills` 或其他安装产物纳入业务 Diff。已存在的非产品
`_bmad` 、Snapshot 不一致、标记被篡改或清理失败均 Fail Closed，不得覆盖用户内容。
对 `candidate_read` Attempt，产品只能在 Provider 子进程启动前临时获得 Detached View
根目录的 owner-write 权限以装配 Overlay；装配后必须立即将 Overlay 和根目录恢复只读，
且 Candidate 跟踪文件全程不得变为可写。Provider 仍在 Codex `read-only` Sandbox 中运行。
最后一个并发 Reviewer 结束后，产品才能以同样的短暂 owner-write 租约移除 Overlay
并恢复原权限。该产品内部租约不赋予 Agent 写权限，也不改变 Reviewer 只读语义。
Codex 子进程只继承运行必需的系统环境白名单和经 Adapter 授权的非敏感 Override；
Feishu、GitHub 等服务 Credential 不得从产品进程环境隐式传入 AgentAttempt。
Party Mode 不在允许入口中。Method 发现资格与真实执行资格必须分开验证；
只能发现 `SKILL.md` 不足以证明 Method 可执行。

本地可信运行时需要复用操作员已经建立的 Codex 登录态时，只允许在临时 `CODEX_HOME`
中创建指向操作员 `auth.json` 的文件引用。该来源必须是当前运行用户持有、Group/Other
无权限的普通文件；凭据内容不得复制进 Method Store、业务仓库、Snapshot、Hash、日志或
Evidence。Overlay 清理只移除引用，禁止跟随链接修改或删除凭据源。此规则只解决本地
Codex Attempt 的 Credential Transport，不改变 Provider Binding、Runtime Identity 或 Live Gate
的证据要求。

## 结果

- 四个角色的 Git 边界与 Agent 组织边界一致。
- Main/Child/Attempt 都成为产品可见实体，可以审计取消、超时、绑定和产物。
- AgentScope 拥有单次 Attempt 内的 Session、消息与 Runtime Transport；ACWM 仍然拥有跨 Stage
  工作流；产品拥有可观察 Workcell Composition，但不复制两者的 Runtime Contract。
- 旧 Delivery、`RepositoryCandidate`、`ReleaseBundleV1` 和历史 Snapshot 保持可读。

## 2026-09-05 修订：按仓冻结产品机器验证方案

Workcell Workspace Governance 拥有机器验证方案选择及工具链资格。产品发布不可变 Profile，
操作者通过具备权限的 Workspace API 选择 ID、执行只读工具资格探针；Agent 不能提供或改写验证命令。
现有 Workspace Git Verification 仍只证明 Git 能力，机器验证资格有独立字段。

Migration 0045 增加可空 Profile ID、资格 Snapshot 和失败原因。修改配置采用 Workspace CAS，
同库事务内通过 Project Port 检查活动 Delivery；正在交付时拒绝修改。资格失败不伪造 ready，
已激活 Team 可在无活动交付时补配。

Delivery/Workcell Snapshot 冻结完整 Profile、Profile Hash、工具路径/版本/二进制 Hash、
qualification Hash。新 Delivery 和 Writer 执行先复核产品 Catalog 与实际工具身份。历史空字段
读取时省略该字段，保持原 Snapshot Hash；不得回填 Python 方案使旧执行获得新资格。

首批只承诺 Python unittest 和 Node native test；不承诺任意 React/pnpm、Design 文档合同或
QA E2E 适配。Python Runner 需先在隔离模块解析环境载入产品选定的标准库，再载入仓库测试；
仓库同名模块不能替换 Runner。固定命令必须发现并成功运行测试，零测试、全跳过、
超时、工具或方案漂移均失败。验证子进程继承受限系统环境；取消/超时必须终止进程组并等待退出，
不得仅取消等待线程后释放项目 Lease。

验证报告记录实际 argv、工具身份、超时、退出码、测试结果合同与日志 Hash，并与原 Candidate/Diff
绑定。Readiness 只证明启动资格，仍为 not_run。具体本地验证结果见本轮执行清单；
此工作区尚未冻结最终 Product Revision，正式 Live Gate 另行验收。

## 2026-09-05 已接受修订：Review 责任与原始证据

Tasking 只提议 `workcell_acceptance`：每个 Workcell 明确引用原始 Acceptance ID 并说明本仓责任。
产品在 Plan Gate 前检查引用、唯一性、冻结 Workcell 集和任务验收覆盖；用户批准的 Gate Subject
包含完整 Requirements 与 Task。不得用产品关键词匹配或把全部验收项默认指派给所有仓来替代该规划。

产品预置 `ReviewPolicySnapshot` 仅冻结既有的允许路径、非空 Candidate 和生成物限制。
Workcell `review_scope` 从批准的 Plan 与该 Policy 快照派生，冻结原始验收正文、责任、Policy
及其哈希；批准后来源改变即失败。Agent 无权改写 Policy，QA Preparation 仍为 Artifact-only。

Reviewer 输出必须绑定 Scope/Candidate/Diff SHA。`code` 是问题分类，不能冒充归属；
每个 Finding 必须且只能引用本 Scope 的 `acceptance_id` 或 `system_policy_id`。
产品先保存每个 Reviewer 的原始 Artifact，再验证合同并登记 Review；一份无效输出不能丢弃
同批其他 Reviewer 的有效 blocker。无效输出走既有 ACWM bounded Loop，不能变成空 Review
或通过 Main synthesis 复活失败 Run。

历史可空字段在序列化中省略，保持旧 Hash；历史可读，新 Workcell 缺 Scope 失败关闭。
状态以 ARCH-20260905-03 为准；专项实现通过不等于最终 Revision 或 Live 验收。

## 2026-09-05 修订：按仓产品验证与 Artifact-only 阶段

Workcell Governance 的 V2 Profile 明确适用 Workcell、固定命令、工具与配置身份、输入/输出包合同。
Agent 只能提交 Candidate，不能重写产品命令、冻结配置或结果计数。执行 Adapter 在临时 Candidate
副本中运行真实工具，发布日志、测试结果及内容寻址产物；产品 Stage 在实际 CandidateVerification
持久化后才登记 Publication，下游只物化这些已验证来源。

QA Preparation 虽共用 QA Workspace Snapshot，但保持 Artifact-only 职责：不写 Candidate、
不运行 QA Delivery 的完整浏览器 Profile、不生产其机器验证包。产品仍校验其 ResultValidation、
原始 Artifact 和 Citation，再允许后续阶段运行。该区分不由模型自行选择。

状态见 `ARCH-20260905-04`；本机真实工具全链回归与真实 Agent/外部 Git Live 验收分开记录。
