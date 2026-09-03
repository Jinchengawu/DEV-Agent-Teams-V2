---
title: Google Teamwork 对 Agent-Team-OS 的启发
document_type: research-note
status: Deferred Evaluation
observed_at: 2026-09-04
truth_scope: external_observation_and_repository_revision_containing_this_file
language: zh-CN
---

# Google Teamwork 对 Agent-Team-OS 的启发

> 本文是外部方案研究记录，不是 ADR、已接受架构、Roadmap 或实施计划。
> 文中候选方向只有在重新完成 Architecture Review、形成最终 Plan 并获得实施授权后，
> 才能进入 `Accepted/Not Implemented` 或实现状态。

## 1. 结论

Google Teamwork 对 Agent-Team-OS 最有价值的启发，不是增加更多子 Agent，也不是引入一个新的
跨 Stage 编排器，而是：

1. 用可选择的协作模式组织 Agent，而不是把固定角色数量当成唯一拓扑；
2. 将 Critic、Challenger 和 Auditor 的职责拆开，使质疑、证伪与证据核验成为正式步骤；
3. 先竞争只读方案，再让唯一 Writer 形成 Git Candidate；
4. 将经过验证的失败经验沉淀为有来源、范围和失效条件的知识；
5. 根据任务风险冻结验证强度，同时保留产品机器验证、人工 Gate 与 Apply 权威。

适合未来探索的目标可以概括为：

> 在 Agent-Team-OS Workcell Kernel 内实现“受治理的 Teamwork 子集”，吸收自适应求解能力，
> 但不破坏 ACWM、Workcell、Workspace 和 Release 的既有权威边界。

## 2. 外部事实与证据边界

Google 官方将 Teamwork 描述为 Antigravity 中面向大型软件工程、复杂模拟和研究任务的多 Agent
协作能力。其核心循环是生成候选、压力测试、综合较强部分并继续迭代；不同任务可以选择不同
Pattern，运行时可以调整 Agent 数量、角色与轮次。

官方公开描述的 Pattern 包括：

- `Iterative Coding`；
- `Distributed Coding`；
- `Long Proof`；
- `Self-Verification`；
- `Document Review`。

公开角色包括 Sentinel、Project Orchestrator、Explorer、Worker、Critic、Challenger、Auditor 和
Success Auditor。官方文档还描述了独立工作目录、文件独占写入、每 Agent Scratch Directory、
结构化 Artifact Handoff，以及 `development`、`demo`、`benchmark` 三种 Integrity Mode。

### 2.1 “开源”表述修正

截至本文观察日期，官方证据可以确认：

- `/teamwork-preview` 是 Antigravity 付费计划中的 Preview 能力；
- `google-antigravity/antigravity-cli` 是公开 GitHub 仓库；
- Google 展示了 Teamwork 对 Eigen、ParlayHash 等开源项目的贡献。

但这些事实不能证明 Teamwork 框架实现本身已经以可复用的开源许可证发布。公开 CLI 仓库当时
可见的主要内容是 README、示例、Changelog 和安装入口，没有足够证据把 Teamwork 当作一个可以
直接 Fork 或嵌入 Agent-Team-OS 的开源依赖。因此未来评估必须区分：

```text
Teamwork 产生了开源项目贡献
!=
Teamwork 框架本身已经开源
```

### 2.2 结果数字的使用边界

Google 公布的数学、CPU 模拟和开源优化结果用于说明其方案潜力，但其任务域、模型组合、并发规模、
评测数据和内部运行环境与 Agent-Team-OS 不同。不得把这些结果直接换算为本项目的质量目标、成本收益
或 Live 验收证据。

## 3. 与当前 Agent-Team-OS 的映射

| Google Teamwork 概念 | Agent-Team-OS 当前边界 | 判断 |
|---|---|---|
| Pattern Spec 与 Agent Description 解耦 | ACWM Published Pipeline Revision 与 TeamTemplate、Agent Profile、Provider Binding 分离 | 原则已对齐，不应建立第二套跨 Stage Pattern 权威 |
| 运行时自适应团队 | Pipeline 冻结 Slot；Workcell 限制 Child 深度、数量、并发和 Writer 数 | 可探索冻结容量内的动态选择，不能无限派生 |
| Explorer | Artifact-only Delegate、只读分析 | 可用于写代码前的多方案探索 |
| Worker | Workcell 唯一 `workspace_write` Child | 应继续保持每 WorkcellRun 最多一个 Writer |
| Critic | 只读 Reviewer | 可继续负责正确性、接口和代码质量审查 |
| Challenger | 当前结构化 Review 的一部分 | 值得拆为独立的对抗测试职责和 Artifact |
| Auditor | Product Machine Verification、Evidence 与 Gate | Agent 只能提供审计材料，不能取代产品机器判定 |
| Success Auditor | Release Acceptance 与人工 Release Gate | 可提供建议性最终审查，不能拥有 Apply 权威 |
| Candidate Tournament | 当前每仓一条 Candidate Lineage | 只能在 Git 写入前进行 Artifact 方案竞赛 |
| Pitfall Registry | Knowledge Index、KnowledgeContextArtifact | 可存储已验证失败知识，不能存储无边界原始聊天记忆 |
| Workspace Isolation | 四个独立 Git Repository Workspace、Writer Worktree、Reviewer Detached View | 当前隔离更严格，应继续保留 |

## 4. 值得保留的未来候选方向

### 4.1 Bounded Adaptive Delegation

Pipeline 发布时继续冻结：

- 可用 Delegate Slot；
- Slot 对应的 Agent Deployment 与 Provider Binding；
- Delegate Purpose；
- Workspace Access；
- 最大 Child 数、并发数、Writer 数和 Wall-clock Budget。

Workcell Main 只允许在冻结集合中选择 `0..N` 个 Child，并持久化可观察的
`DelegationDecision`。运行时不得发现未冻结 Provider、创建 Hidden Child，或允许 Child 再派生。

该方向提供有限自适应能力，但不会建立第二套 Graph、Stage 或生命周期权威。

### 4.2 独立 Challenger Artifact

未来可以把 Reviewer 的对抗职责拆成 `ChallengeArtifact`，至少绑定：

- Candidate SHA 与 Diff SHA；
- Challenger Binding Hash；
- 边界条件、失败路径和最坏输入；
- 实际执行命令与输出 Artifact；
- 可重放的 Failure Finding；
- Finding 是否 Blocking。

Challenger 负责提出攻击假设和生成测试，Product Machine Verification 负责执行或核验。Main 不得覆盖
机器失败或 Blocking Finding。

### 4.3 写入前的 Artifact Strategy Tournament

对于高不确定性任务，可以在唯一 Writer 之前运行只读探索：

```text
Explorer A 候选方案 ┐
Explorer B 候选方案 ├→ Main 综合与选择 → 唯一 Writer → Git Candidate
Explorer C 证伪报告 ┘
```

各 Explorer 只交换内容寻址 Artifact，不挂载其他 Workcell 仓库，不产生并行 Candidate Branch。
最终仍保持每个 Design、Frontend、Backend、QA 仓库在一个 Delivery 中只有一条 Candidate Lineage。

### 4.4 Verified Pitfall Registry

失败经验只有经过产品验证后才可以进入知识索引。候选 `PitfallArtifact` 应包含：

- Project、Workcell、Method Pack、模型和工具版本范围；
- 失败症状、根因及证据；
- Candidate、Diff、Attempt、Verification Artifact 引用；
- 适用范围、失效条件和可选 TTL；
- `observed`、`verified`、`superseded` 等状态。

检索到的 Pitfall 只能作为带 Citation 的非权威上下文。它不能覆盖 Pipeline、Artifact Contract、
机器 Verification、Approval 或 Release Manifest，也不能因一次模型推断自动升级为规则。

### 4.5 Execution Integrity Profile

Google 的 Integrity Mode 提醒我们：验证强度应成为冻结的执行政策，而不是 Prompt 中的临时要求。
若未来引入类似能力，应由 Published Pipeline/Project Governance 选择并写入
`DeliveryExecutionSnapshot`，例如：

- 快速开发：允许较轻验证，但仍禁止伪造执行结果；
- 可复现演示：要求干净环境重放和完整证据；
- 正式发布：要求同 Revision、零跳过 Gate、真实 Provider 与远端回读。

该 Profile 只能调整验证要求，不能降低权限、凭据、Workspace 隔离和 Apply 安全边界。

## 5. 明确不采用的方向

后续讨论 Teamwork 时，以下内容默认不进入方案：

- 用第三方 Teamwork Runtime 替代 ACWM 或 Workcell Execution；
- 运行时自由生成 Stage、修改已发布 DAG 或发现新 Provider；
- Hidden Child、二级子 Agent 或不可观察的内部派生；
- 四个角色共享 Git Workspace，或跨 Workcell 挂载其他角色仓库；
- 同一 WorkcellRun 多个 Writer 同时修改同一 Repository；
- 让 Agent Critic/Auditor 的文字判断覆盖机器验证；
- 让 PR Provider、模型或外部 Runtime 获得 Apply/Manifest 权威；
- 将原始聊天、未核验失败草稿或敏感仓库内容直接写入 RAG；
- 用 Google 的内部指标替代本项目 Evaluation Dataset 与 Live Release Gate。

## 6. 未来重新评估条件

只有同时具备以下条件，才值得把本记录升级为正式架构提案：

1. 当前四 Workcell Kernel 在同一 Revision 上完成稳定的 Candidate、Verification、Review、PR、
   Forward-only Apply 与 Manifest 闭环；
2. Deterministic 与 Live 证据已经明确分离，并有可重复的基线；
3. 能稳定观测 AgentRun、AgentAttempt、Token/Cost、Wall-clock、Repair Loop 和人工介入；
4. 已积累足以证明固定 Workcell 拓扑存在瓶颈的真实失败样本；
5. 能说明自适应选择为何不应由现有 ACWM Pipeline 直接表达；
6. 可以在不改变四仓隔离和唯一 Writer 的条件下完成受限实验；
7. Google 对 Teamwork 的接口、许可、数据使用和部署边界有足够公开证据，或我们明确只借鉴思想、
   不引入其实现。

未达到这些条件时，优先完成当前 Release 与 Live Readiness，不把多 Agent 数量当作完成度。

## 7. 可供未来讨论的最小实验，不是实施计划

若重新评估条件满足，可以先在单个非关键 Workcell 上启用 Feature Flag：

```text
adaptive-workcell-v1
→ Main 从冻结 Slot Pool 选择 0..N Explorer
→ Artifact Strategy Tournament
→ 唯一 Writer
→ Product Machine Verification
→ Critic + Challenger 只读审查
→ Main Synthesis
→ 原有 WorkcellResult Validation 与 Release Gate
```

实验不得修改现有 Release/Apply 状态机。至少对照以下三组：

1. 单 Agent；
2. 当前固定 Workcell；
3. 受约束自适应 Workcell。

应比较：任务通过率、逃逸缺陷、Repair Loop 数、重复 Attempt、Token/Cost、Wall-clock、人工介入次数和
证据完整性。只有在质量提升能够覆盖成本、延迟和治理复杂度后，才考虑扩大范围。

若该实验进入正式 Plan，预期 `Architecture Impact` 为 `Cross-boundary`，并需要 ADR，因为它会改变
Workcell 内 Delegation、Review Artifact 与 Knowledge Learning 的协作语义。

## 8. 来源

- [原始中文文章：Google 重磅发布 Teamwork](https://mp.weixin.qq.com/s/hlwC9xBDNcaMkHkrnuP9eA)
- [Google Antigravity Blog：Teamwork: When AI Becomes a Research Partner](https://www.antigravity.google/blog/teamwork-when-ai-becomes-a-research-partner)
- [Google Antigravity Docs：Teamwork agent teams](https://antigravity.google/docs/teamwork/)
- [Google Antigravity CLI GitHub 仓库](https://github.com/google-antigravity/antigravity-cli)
