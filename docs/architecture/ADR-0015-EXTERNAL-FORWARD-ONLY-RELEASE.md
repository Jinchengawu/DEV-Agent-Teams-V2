# ADR-0015：外部 Git 的 Forward-only Release

状态：已接受
日期：2026-08-31
修订：2026-09-05，P0-01 取消、发布提交与恢复准入补强

## 背景

ADR-0012 为受管 Bare Git 的四仓 Release Bundle 定义了 CAS Compensation。外部 GitHub
仓库的事实不同：产品不拥有远端仓库生命周期，已推进的 `main` 也不应被 Force Push
或自动回滚。

## 决策

外部 Git 采用 `external-forward-only-v1`：

1. 仅绑定已存在的私有 GitHub HTTPS 仓库；不创建、删除或重命名远端仓库。
2. Credential 只保存 `env://` 或 `keychain://` 引用，不进入 API 回执、SQLite、日志、截图或 Git。
3. Candidate Branch 固定为 `agent-team-os/{delivery_id}/{workcell_key}`，只允许非 Force Push。
4. GitHub PR 仅是 Review Surface。`GitHubPRReceipt` 必须绑定 Base、Head Branch 和精确
   Candidate SHA，但 GitHub Merge 不具有 Apply 权威。
5. Release Gate 绑定不可变 `ReleaseBundleV2.bundle_sha256`。
6. Apply 前重新 Fetch，要求远端 `main == reviewed base`；然后用非 Force Fast-forward Push
   将精确 Candidate 推进到 `main`。
7. Push 后重新读取远端 SHA，必须等于 Candidate SHA，才持久化 `RemoteApplyReceipt`。
8. 四仓按冻结 Release Contract 顺序执行。首次失败立即停止；已成功仓库不回滚、
   不 Force Push。
9. 部分成功时 Delivery 进入非终态 `needs_attention`，Project 进入 `release_drifted`，
   Delivery Lease 继续持有，不激活 Manifest。
10. `resume-forward` 只接受原 Bundle：已应用仓必须仍等于 Candidate，未应用仓必须仍等于 Base。
    不满足时继续人工协调，v0.5 不重写 Bundle、Rebase 或生成补偿提交。
11. 全部远端回读成功后才激活 `ReleaseManifestV2`；Manifest 中的四个 SHA 必须与回执一致。

## 取消、拒绝与 Apply 的并发裁决

本轮修订的 `Architecture Impact` 为 `Critical`。Delivery、Project Lease 和外部发布状态
必须共同保证：通用取消不能让已经发生的外部副作用失去恢复所有者。

1. `DeliveryRepository.save_if_current()` 比较持久化的 Version 与 Status，只有两者均匹配
   才提交 Delivery Snapshot 和 Product Event。SQLite 使用 `BEGIN IMMEDIATE` 内重读与写入；
   InMemory 实现对读取、保存和 CAS 使用同一 `RLock`。CAS 失败返回
   `DeliveryTransitionConflictError`，不写事件、不释放 Lease，也不继续执行副作用。
2. Candidate 接受通过 CAS 将 `awaiting_candidate_decision` 改为 `applying`，之后才能执行
   Apply。Cancel 与 Candidate Reject 先通过 CAS 进入非终态 `cancelling`，之后才能取消
   背景任务、Graph 和 Child。竞争失败方不得取消胜者的执行，也不得推进远端 Git。
3. `applying` 和 `needs_attention` 拒绝通用 Cancel。过期请求与既有终态保护继续生效；
   `cancelling` 与 `needs_attention` 都属于活动交付，不能因为进入这些状态而允许另一条交付启动。
4. Candidate Reject 在进入 `cancelling` 时冻结 `candidate_gate.decision=reject`；清理成功后
   进入 `rejected`。其余取消清理成功后进入 `cancelled`。恢复与重试使用持久化的审批决定，
   不改变已经确定的 Reject/Cancel 意图。
5. 取消入口在协调器的事件循环中异步等待清理。不得取消或等待当前清理任务自身；对已经收到
   取消的背景任务，不重复发送取消以中断其异步清理。等待受保护的清理任务；请求自身被取消且
   清理尚未完成时，保留 `cancelling`，不能提前报告终态。
6. `cancelling` 持续持有 Lease。清理异常保留该状态与错误证据，重启后继续幂等清理；只有清理
   成功并持久化终态后，才允许释放对应 Lease。终态 Workcell 的取消请求必须先遵守 Kernel
   的状态与版本规则，不能通过一个已经结束的 Workcell 取消仍活动的父 Delivery。
7. 过期后台审批请求不能覆盖竞争胜者。真实执行错误仍保留错误证据，但不能将
   `cancelling/applying/needs_attention` 转为可释放的普通失败状态，也不能用旧后台错误覆盖
   已提交的 `completed/rejected/cancelled/failed`。

`cancelling` 是产品可见的非终态，不使用“已取消但仍隐藏清理中”的成功标记。
该状态是 Delivery JSON 契约的新增取值；本轮不回填历史 Snapshot，也不改变历史 Runtime 证据身份。

## 发布恢复所有者与 Project Lease

- Release 模块拥有恢复事实：未完成的 `ReleaseApplyAttemptV2`，以及 `release_drifted`
  Health 所引用的 Delivery。Project 通过组合根注入的只读查询 Port 获取恢复所有者，
  不直接读取 Release 表，也不另建一份发布健康事实源。
- 存在恢复所有者时拒绝创建新 Delivery。即使历史错误已将 Delivery 写成终态、或者 Lease
  已经丢失，仍不能据此认为外部发布完成。准入检查不修写历史 Delivery；恢复继续遵守原 Bundle。
- `prepare_delivery` 先检查恢复约束；首次保存 Delivery 与获取 Project Lease 的
  `BEGIN IMMEDIATE` 事务内再次检查，封闭检查与保存之间的并发窗口。
- 同步事务依赖 Release、Project 和 Delivery Repository 指向同一个 SQLite 数据库文件；
  组合根校验该前提。事务中的恢复查询只读，不执行网络访问或写操作。该保证不适用于任意跨库部署。
- 当前 Workcell Workspace-Set 缺少恢复查询时 Fail Closed；Legacy Managed 项目保留不依赖
  外部 Release Guard 的运行方式。
- 通用终态保存与 `reconcile_leases()` 也必须检查恢复所有者。重启时保留或重建该所有者的
  Lease，不能把它作为普通重复 Delivery 降为 `failed`。发现多个未完成发布所有者时拒绝自动
  选择和降级，保留既有事实并要求协调。

## 同库原子提交与异常恢复

远端推进与 SHA 回读仍按冻结 Release Contract 顺序执行；远端操作不进入 SQLite 事务。
已回读的 `RemoteApplyReceipt` 先持久化，以支持中断后辨认哪些仓库已经推进。

四仓回读全部满足 Candidate 后，以下完成事实在同一个数据库事务中提交：

- 激活与原 Bundle 绑定的 `ReleaseManifestV2`；
- 按预期 Version 将同一 `ReleaseApplyAttemptV2` 置为 `completed`；
- 将 Project Release Health 置为 `healthy`；
- 写入 Delivery `completed`、Manifest Hash 与 Product Event；
- 释放该 Delivery 的 Project Lease。

远端执行与上述本地最终提交都纳入失败处理。异常后先回读权威完成事实：如果同一 Bundle
已经完整提交，返回既有结果，不把成功降回 `needs_attention`。否则以最新 Attempt Version
保存恢复状态与错误；数据库无法写入时继续上抛错误，不能用通用 `failed` 掩盖未完成的发布。
已持久化的未完成 Attempt 或 Drift Health 仍作为恢复与准入保护依据。

本机制不是跨远端仓库的分布式原子事务。已经成功的远端不回滚；未完成的发布仍只允许原 Bundle
的 `resume-forward`，没有新增 Force Push、Rebase、Bundle 重写、自动补偿或放弃恢复语义。

## Readiness 边界

Team Activation 要求每个 Workspace 存在 `main`、凭据可用且服务身份通过非 Force
Fast-forward `main` 能力检查。任一仓库不允许直接推进 `main` 时返回
`REMOTE_MAIN_APPLY_NOT_ALLOWED`，该项目不能通过 v0.5 Live Readiness。

## 本轮验证与证据边界

2026-09-05 的本地工作区验证已记录以下结果：

| 验证集合 | 结果 | 覆盖边界 |
|---|---|---|
| [取消与 CAS 合同](../../tests/test_delivery_cancellation_safety.py) | 26 passed | SQLite/InMemory 竞争、Cancel/Reject/Apply 裁决、公开取消 API、异步清理与重启、重复取消、后台旧错误及 Workcell 终态路由保护 |
| [External Forward-only Release V2](../../tests/test_external_forward_release_v2.py) | 7 passed | Partial Apply、原 Bundle 恢复、Drift 拒绝、原子最终提交失败、提交回执丢失、最新 Attempt Version 与历史投影恢复 |
| [Project Release Recovery Guard](../../tests/test_project_release_recovery.py) | 10 passed | 公开 API 阻断、Lease 保留与重建、首次保存事务复查、多恢复所有者拒绝、Guard 缺失与跨库组合拒绝、Legacy 兼容 |

上述结果使用临时 SQLite、InMemory、Deterministic Adapter 或 FakeRemote。当前工作区尚未冻结
最终验收 Git Revision；这些结果证明本轮本地状态、并发与恢复合同，不是 Live Git 证据，也不是
正式 Major Release Report。核心浏览器、Deterministic 与 Live 同 Revision 零容差验收仍须
另外完成，Live 目前仍未验收。

## 与 ADR-0012 的关系

本 ADR **只取代 ADR-0012 的外部 Git 应用策略**。现有 Managed Bare Git、
`ReleaseBundleV1`、CAS Compensation 和历史 Manifest 语义保持不变。

Workspace-Set 跨 Delivery 并发、Delta ReleaseBundle、Manifest Version CAS 和
Provider-native PR Merge 延后到 v0.6。
