# ADR-0020：v0.5.1 Delivery Bundle 与 Product Root

状态：已实现并完成本地合同验证
日期：2026-09-20
架构变更：`ARCH-20260920-01`

## 背景

Preview Runtime 曾从 `Path(__file__).parents[2]` 推导 config、Migration 和 Console 位置。
该假设只对源码 checkout 成立，Python wheel、Console dist 和运行资源也没有一个绑定
同一产品 Revision 的交付单元。

## 决策

1. 交付单元是版本化 `Delivery Bundle`，而不是宣称 wheel 单独自包含。
2. Bundle 以 allow-list 组合后端 wheel、`console/dist`、checksummed Migration、锁定 config 和
   Preview `EvaluationService` 启动必需的默认公开 Evaluation Dataset；
   Manifest 记录产品版本、Git SHA、相对路径、大小和 SHA-256。
3. `AGENT_TEAM_OS_PRODUCT_ROOT` 是显式运行配置。一旦设置，Root 必须完整且校验通过；
   非法时 fail-closed，不得静默回退到源码仓。未设置时保留 checkout 开发兼容。
4. Bundle 拒绝符号链接、数据库、日志、缓存、`.env`、密钥和凭据型文件；运行数据
   继续由独立 `AGENT_TEAM_OS_DATA_DIR` 持有。
5. Manifest 是制品完整性事实，不拥有 Release Gate、Approval 或 Apply 判定权。
6. 正式 Bundle 拒绝 dirty worktree；开发例外必须在 Manifest 标记 `development_only`，
   不得冒充与 Git SHA 一致的冻结候选。

## 权威与兼容

- 不改变 ACWM、Workcell、Verification、Release Acceptance 或 Forward-only Apply 权威。
- 不改变数据库 Schema 或历史 Snapshot。
- 源码 checkout 无显式 Product Root 时保持现有开发路径。
- 旧 `0.5.0` 制品继续作为历史文件，不被追认为 `0.5.1` Bundle。

## 被拒绝的方案

- 将全部仓库复制到交付包：会携带运行态、开发依赖和非必要文件。
- 显式 Root 无效时回退 checkout：会产生伪 clean-room 通过。
- 新建交付 Gate：会复制现有 Handoff Evidence 和 Release Acceptance 权威。

## 验证边界

版本一致性、Bundle 正反合同、Manifest 篡改拒绝、真实 Evaluation Dataset 加载、Product Root 显式选择和无回退由
自动化测试覆盖。本地 Bundle 成功只是 Local evidence；Deterministic、Live、Release Approval、
Apply 与 read-back 仍需各自的同 Revision 证据和授权。
