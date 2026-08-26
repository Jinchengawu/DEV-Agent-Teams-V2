# ADR-0012：多仓库 Repository Set 与可恢复 Release Bundle

状态：已接受
日期：2026-08-26

## 背景

ADR-0009 将一个 Delivery 内所有 Git 修改串行化，ADR-0011 为每个 Project 配置一个 Bare
Repo。产品规划、设计、前端、后端和 QA 的完整闭环要求代码实体独立管理，同时仍需保留
审批、证据、冲突和恢复语义。

## 决策

Project Governance 使用 Project Repository Set 管理 `backend`、`design`、`frontend` 和
`qa` 四个独立仓库。Delivery 启动时冻结 Repository Set Snapshot 和内容哈希。

- 同一仓库内的 Git 修改保持串行。
- 不同仓库的 Agent Stage 可以并行执行。
- Project Delivery Lease 继续限制一个项目最多一个活动 Delivery。
- 四仓候选组成一个 Release Bundle；Release Coordination 负责预检、协调 CAS、回滚、
  重启恢复和 Release Manifest。
- 跨仓库 Git Ref 不具备物理原子性。产品只在四仓 Main 全部等于已审查 Candidate，且
  Active Release Manifest 指向同一组 Revision 时声明完成。
- 第三方 Revision 或回滚冲突进入人工协调状态，不自动合并或强制覆盖。

本 ADR 取代 ADR-0009 中“所有 Git 修改全局串行”的范围，并取代 ADR-0011 中“一项目一
Bare Repo”的实现约束；其他不变量继续有效。

## 兼容

现有 ProjectWorkspace 作为 Backend-only 兼容视图保留。旧项目不会自动创建新仓库；管理员
显式执行全栈 Provisioning 后才能选择 Full-stack Pipeline。
