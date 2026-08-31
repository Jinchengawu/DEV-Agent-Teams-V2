# ADR-0015：外部 Git 的 Forward-only Release

状态：已接受
日期：2026-08-31

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

## Readiness 边界

Team Activation 要求每个 Workspace 存在 `main`、凭据可用且服务身份通过非 Force
Fast-forward `main` 能力检查。任一仓库不允许直接推进 `main` 时返回
`REMOTE_MAIN_APPLY_NOT_ALLOWED`，该项目不能通过 v0.5 Live Readiness。

## 与 ADR-0012 的关系

本 ADR **只取代 ADR-0012 的外部 Git 应用策略**。现有 Managed Bare Git、
`ReleaseBundleV1`、CAS Compensation 和历史 Manifest 语义保持不变。

Workspace-Set 跨 Delivery 并发、Delta ReleaseBundle、Manifest Version CAS 和
Provider-native PR Merge 延后到 v0.6。
