# ADR-0019：四仓 Delivery 的 Release Acceptance V2

状态：已实现并完成 Deterministic 验证；Live `blocked/not_run`
日期：2026-09-02
架构变更：`ARCH-20260902-04`

## 背景

`knowledge-live-readiness` 只能证明真实 Feishu/Ollama/Hermes、四仓 Workspace、Published
Pipeline 和 Runtime Binding 已具备启动条件。它不运行 Delivery，也不证明最终结果已被产品接纳。

现有 `GateReport` 服务于 Legacy Managed Git V1：只携带一个 Candidate/Diff/Verification，并保留
旧 Planning Identity 语义。将四仓 Workcell、Knowledge Context、Main/Child/Attempt、Forward-only
Apply 和 `ReleaseManifestV2` 填进该模型，会同时破坏 V1 兼容和 V2 证据可读性。

此外，当前 `DeliveryExecutionSnapshot` 没有冻结运行该 Delivery 的 Agent-Team-OS Git Revision
与 ACWM Dependency Attestation。仅在报告生成时读取 `HEAD`，不能证明执行和验收发生在同一
Revision。

## 决策

新增独立的 `Release Acceptance V2 Module`，不修改 Legacy `GateReport` 的历史语义。

### Build Identity

新的 Workcell Delivery 在 `DeliveryExecutionSnapshot` 中冻结内容寻址的
`DeliveryBuildIdentitySnapshot`：

- Agent-Team-OS Git Revision 及创建 Snapshot 时的 Worktree Clean 状态；
- ACWM 实际 Revision；
- `framework-lock.json` 内容 Hash；
- Framework Lock、`pyproject.toml` 精确版本/Git Revision、`uv.lock` 实际解析 Revision 与
  imported ACWM 的 Dependency Attestation 状态；
- 上述完整 Build Identity 的内容寻址 Hash。

Legacy Snapshot 允许该字段缺失以保持读取兼容；缺失、Dirty 或未通过 Attestation 的 Snapshot
不能生成通过的 V2 Live Report。

### 验收 Interface

Verifier 只接受一个已经完成的 Delivery，不启动 Agent、不重新检索、不创建 Candidate、不 Apply，
也不修改任何领域状态。它通过现有 Repository Interface 汇总并交叉验证：

- 报告时 Git Revision 与冻结 Build Identity 完全一致，两个 Worktree 均可重放；
- Delivery 与 Pipeline Run 为成功终态；全部 WorkcellRun 均已终止，每个必需
  Stage 的最终 Loop Iteration 必须成功。历史 Repair Iteration 可以保留失败事实；
- 每个 Workcell Snapshot 不仅校验自身 Hash，还必须逐字段绑定回 Delivery Snapshot 中冻结的
  Team、Pipeline、Workspace、Method、Knowledge Artifact 与 Provider Slot；同步重算一个伪造的
  Workcell Snapshot Hash 不能绕过验收；
- Requirements/Tasking 使用冻结的真实 `hermes.acp` Binding，全部 Workcell Slot 使用真实
  `codex.cli` Binding；每个 Planning Root 只能有一个 `legacy(1)` Attempt，Workcell Main 必须精确
  保留 `planning(1) → synthesis(2)` 两次 Attempt，Child 只能有一次 `delegate(1)` Attempt；Root/父子
  拓扑、DelegationPlan、Workspace Access、Method、Artifact 与 Binding Hash/Runtime Identity 均匹配
  冻结 Snapshot；
- Required Knowledge Context 全部存在，Citation 只来自冻结 Context，且授权 Stamp 在结果接纳时
  仍有效；
- Release Contract 精确产生 Design、Frontend、Backend、QA 四个 Candidate，每个 Candidate 均有
  Verification、Blocking Finding 为零的 Review 和 GitHub PR Receipt；
- `ReleaseBundleV2`、四个 `RemoteApplyReceipt`、远端 SHA 回读与 Active
  `ReleaseManifestV2` 完全一致，Release Health 为 `healthy`。

Report 使用内容寻址 Hash，且不保存 Secret、Credential Reference、Repository URI、知识正文或
模型原始响应。`FAIL=0`、`WARN=0`、`skipped=0` 才能标记 `passed`。

### Readiness 与 Report 状态

```text
Readiness blocked → execution_status=not_run → 不生成 Release Report
Readiness ready   → 验证既有 completed Delivery
                 → passed | failed
```

`ready/not_run` 永远不是 Live 验收。Verifier 失败只生成失败 Report，不回滚或改写 Delivery、Bundle、
Receipt、Manifest。

## 权威与关系

- ADR-0015 继续唯一拥有 External Forward-only Apply、`needs_attention` 和 `resume-forward`；
- ADR-0018 继续拥有 Knowledge Context、Citation 和撤权接纳规则；
- 本 ADR 只拥有“这些既有事实如何组合成 Major Release 验收证据”的语义；
- Legacy V1 Gate 与 Managed Git CAS Compensation 不变。

## 兼容与迁移

- `DeliveryBuildIdentitySnapshot` 是新 Snapshot 的可选字段；历史 JSON 不迁移、不回填；
- 缺失该字段的历史 Delivery 可继续查看，但不能被追认成 V2 Live Release；
- Report 使用新的 Schema/目录，不进入 V1 `/v1/release-gates/*` 投影；
- 本 ADR 不创建新的 Apply Authority，也不改变 Project 单活动 Delivery Lease。

## 验证

- Build Identity 对 Dirty Worktree、Framework/Dependency/Resolved Revision 漂移失败关闭；
- Deterministic R2 四仓 E2E 已验证 7 个 Knowledge Stage、5 个 Workcell（含 Artifact-only
  QA Preparation）、4 个 Candidate/PR、Forward-only Apply、Manifest 和 Release Health；
- QA Preparation Validation Hash、重新计算 Hash 的伪造 Workcell Snapshot、Hermes Planning/
  Workcell Main Attempt Phase、Knowledge Stage Result 与 Report Hash 篡改均会 Fail Closed；报告中
  不存在 Secret、Credential Reference、Repository URI 或正文；
- 真实 Live 验收仍必须使用真实 Tenant/Ollama/Hermes/Codex 和四个 GitHub 私仓，并满足
  `FAIL=0`、`WARN=0`、`skipped=0`。

## 2026-09-05 修订：验证方案与结果证据绑定

Release Acceptance 逐字段比较 Delivery 与 Workcell Workspace 中冻结的 Verification Profile。
Writer 的 CandidateVerification 必须包含同一 Profile/qualification Hash、工具身份、结果合同，
实际命令及超时必须等于冻结方案；退出码和零测试/跳过规则均通过，日志内容与 Hash 一致。
只重新计算自洽的 Report/Snapshot Hash，不能替换产品发布的验证方案或改变验证命令。

历史验收校验冻结 Profile 与其资格 Hash，不执行今天的工具探针，不因今天工具升级推翻旧结果；
新的执行资格另由 Stage Driver 检查。未冻结 Profile 的历史结果可以读取，但不能满足本版本新增的
正式机器验证要求。旧 Snapshot 的空字段序列化不得改变原 Hash 输入。

Knowledge Live Readiness 新增独立的按仓机器验证资格检查，四仓均通过才具备启动资格；
检查通过仍为 execution_status=not_run，不是机器测试、Agent 执行或完整 Live 成功证据。
