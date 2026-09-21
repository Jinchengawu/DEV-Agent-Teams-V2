# ADR-0021：Project Onboarding 与 Setup Readiness 失败关闭

状态：已实现并完成本地合同验证
日期：2026-09-20
架构变更：`ARCH-20260920-02`

## 背景

Project lifecycle 已是项目资源生命周期权威，不能同时表示首次评测引导。
Runtime、Workspace、Pipeline、Deployment、Verification 和可选 Knowledge 的就绪事实已分别存在，
但 Delivery 创建缺少一个与 Console 同源的服务端失败关闭入口。

## 决策

1. `project_onboarding` 作为 Project Governance 拥有的独立持久化状态，仅允许
   `setup → in_evaluation → ready`；它不修改既有 Project lifecycle。
2. 既有 Project 幂等回填为 `standard/ready`，新 `guided_evaluation` Project 从 `setup`
   开始。onboarding 记录使用 version CAS，并最多绑定一条评测 Delivery。
3. Setup Readiness 是对现有运行时与项目权威的即时只读组合，不持久化成
   新的能力事实。`navigation_target` 只能是受控产品内部路由。
4. Delivery 通过 `purpose=product|onboarding_evaluation` 表达用途。服务端在创建前
   重新组合 Readiness，阻塞时返回 `409 DELIVERY_READINESS_BLOCKED`；Console 禁用
   动作不是安全边界。
5. onboarding evaluation Delivery 的初始化、活动 Lease 与 onboarding 绑定在同一
   SQLite 事务中提交，避免可见的半绑定状态。
6. 完成 onboarding 必须由 Administrator 提交当前 CAS version、已绑定 Delivery
   和预期 Candidate Gate subject SHA。服务端只引用既有 Gate、Verification 和 Evidence
   事实，不生成 Release Approval 或 Apply 结论。

## 权威与依赖方向

- Project Governance 拥有 onboarding；Delivery 模块通过 Project Application Interface 读取并准入。
- ACWM 继续拥有 cross-stage workflow 与 Gate 语义；本变更不复制其 Runtime Contract。
- Agent-Team-OS 继续拥有可观测 Delivery、Verification、Evidence、Approval 和 Apply 策略。
- Setup Readiness 只组合已有事实，不得回写 Runtime、Deployment 或 Verification 状态。

## 兼容与恢复

- Migration 0046 为既有 Project 回填 `ready`，不改变历史 Delivery 或 Project lifecycle。
- API 新字段具有兼容默认：Project 默认 `standard`，Delivery 默认 `product`。
- 进程重启后 onboarding 从 SQLite 恢复；Readiness 在每次读取或创建时重算。
- CAS 冲突、Readiness 漂移、Gate subject 不匹配或 Evidence 缺失均失败关闭。

## 被拒绝的方案

- 复用 Project lifecycle 表示引导：混淆资源生命周期与用户引导。
- 仅在 Console 禁用启动按钮：无法阻止直接 API 调用与确认期间漂移。
- 持久化聚合 Readiness：会创建与 Runtime/Project 事实竞争的第二权威。
- 完成 onboarding 时自动 Apply：跨越用户授权与 Release/Apply 信任边界。

## 验证边界

Migration、onboarding 转换、Readiness 公共接口、Delivery 409、原子绑定、权限和
Console 主路由由本地自动化测试覆盖；完整 Deterministic Browser 轨道已达到
Candidate/Evidence/onboarding ready，并断言无 Apply、Release 或远端 Git 写入。该证据支持
`Local Contract Verified / Deterministic Browser Verified`，但不替代真实 Codex 或正式 Live。
真实 Codex local-PR 轨道已运行但未通过，fresh 重跑受外传授权阻塞；
Release Approval、Apply 和 read-back 仍是独立验收事件。
