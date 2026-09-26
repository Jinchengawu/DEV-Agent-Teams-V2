# 下一阶段 S2 / NR-02、NR-08 本地公共界面预验证（2026-09-24）

状态：`S2 子集 Local Accept / 正式 S2、NR-02、NR-08 未退出`。本文件只归档本机公共
API 与 jsdom 组件的观察，不是运行实例、真实浏览器、四仓资格或原生 Gate 报告。依据
[下一阶段计划](../NEXT-STAGE-DELIVERY-PLAN.md) S2/S3、
[产品验收合同](../NEXT-STAGE-DELIVERY-READINESS.md) NR-02/08 与
[ADR-0021](../../architecture/ADR-0021-PROJECT-ONBOARDING-SETUP-READINESS.md)。

## Draft Plan → Architecture Review → Revise Plan → Final Plan

Draft Plan：仅新增本文件，回读当前 HEAD 和已有 dirty 状态；记录五项 Python、十一项 Console
定向测试的命令/退出码，另将首次误路由的 Console 命令标为中断且不计数。不得改已有证据或源码。

| Architecture Review 字段 | 结论 |
| --- | --- |
| Architecture Impact | `None`，只是事实归档。 |
| Findings | Project Governance 的 onboarding、只读 Setup Readiness 和服务端 Delivery 准入权威不变；ACWM 跨 Stage 与产品 Gate/Apply 权威不变。没有 Module/Port/Adapter 依赖、数据所有权、状态机、并发/恢复、权限/Workspace、Legacy/Migration 或外部集成策略变更。jsdom 的 stub `fetch` 不是运行时 API 联动证据；TestClient/ASGITransport 也不是浏览器验收。 |
| Required Revisions | 将 `/v1/readiness` 旧接口 503 与 Setup/创建的同源 409 分开；注明首次 Vitest 误路由无 summary；将 Editor API 放行与 Editor UI 入口区分，并列出资格、真实浏览器/移动/键盘的缺口。 |
| ADR Required | `No`；不跨 ADR 门槛。 |
| Architecture Document Delta | 无，不编辑架构总览或 ADR。 |
| Outcome | `Approved`，只批准新增本局部证据页，不提升 S2、NR、业务 Gate 或 Apply 状态。 |

Revise Plan：按审查意见区分 API、UI mock 与未验证项。Final Plan：只新增本文件，回读
Hash、Git 状态、行尾空白和敏感内容。Implementation：仅本文件。
Architecture Reconciliation：无运行行为或架构事实变化。

## 身份、命令和原生结果

- 本次测试的 `HEAD=dbd3bbb651aaeb11693d9f1efb37bad6c3842fa8`。前后 Git 状态相同：
  原有三份 tracked 文档修改及 `docs/product/evidence/` 未跟踪文件均保留。本文件是唯一新增项；
  本次未 commit/push。产品 Agent 经主任务转达，仅对下列 5+11 项子集作 `Local Accept`，
  并未批准正式 S2 或 NR 退出；本文件也不是独立签名回执。
- Python（仓库根目录）：

  ```text
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/test_readiness_api.py tests/test_interaction_closure.py::test_product_delivery_is_blocked_until_guided_onboarding_is_ready tests/test_interaction_closure.py::test_setup_readiness_and_create_share_the_same_runtime_blocker tests/test_identity_http.py::test_product_api_requires_session_csrf_and_permission tests/test_project_authorization.py::test_delivery_creation_requires_global_and_project_capability
  ```

  `exit 0`；pytest 原生摘要 `5 passed in 1.64s`。`TestClient`/`ASGITransport` 在进程内调用
  API；需要持久化的测试只使用 `tmp_path` 临时 SQLite，Planning/Execution 为 Deterministic
  fixture，没有连接旧业务数据库、运行实例或真实 Provider。
- Console 首次命令（仓库根目录）：

  ```text
  pnpm --dir console test -- src/features/setup/SetupPage.test.tsx src/features/deliveries/DeliveriesPage.test.tsx src/features/identity/LandingRoute.test.tsx
  ```

  该 `--` 被实际脚本传给 Vitest，开始发现超出指定文件的用例；发现误路由后立即中断。
  会话结束时工具返回 `exit 0`，但没有 Vitest 总结行，**不计为通过或失败**，也不纳入
  11 项。随后改为本地可执行文件精确过滤，不并发运行。
- Console 定向重跑（工作目录 `console`）：

  ```text
  node_modules/.bin/vitest run --reporter=verbose --pool=forks --poolOptions.forks.singleFork=true src/features/setup/SetupPage.test.tsx src/features/deliveries/DeliveriesPage.test.tsx src/features/identity/LandingRoute.test.tsx
  ```

  `exit 0`；Vitest 原生摘要 `Test Files 3 passed (3)`、`Tests 11 passed (11)`，Duration
  `3.07s`。三个文件都使用 jsdom 与 stub `fetch`，不访问网络、真实 API 或 8117 实例。

## NR-02 / NR-08 可复核映射

| 场景 | 本地证据 | 不能推导 |
| --- | --- | --- |
| Readiness UI/API 阻断同源 | `test_setup_readiness_and_create_share_the_same_runtime_blocker`：同一 `runtime.codex-cli` blocker 在 Setup GET 中为 blocked，Delivery POST 返回 409，`context.checks` 与该 blocker 相等；`test_product_delivery_is_blocked_until_guided_onboarding_is_ready`：onboarding 未 ready 时 POST 409。`SetupPage.test.tsx` 显示 mock 的同一阻断、项目深链和内部修复入口；`DeliveriesPage.test.tsx` 阻断时禁用确认且不发 POST。 | UI 侧 `fetch` 是 stub，不证明它已与一个真实实例的 API 同步；只测部分 blocker，不证明四仓 Verification Qualification 有效。 |
| 请求失败不乐观放行 | `test_readiness_api.py` 的旧 `/v1/readiness` 在缺 Runtime 时返回 503 和修复建议；`DeliveriesPage.test.tsx` 将模拟的 Setup GET 503 显示为 fail-closed，确认按钮禁用、不发 POST。 | 旧接口 503 不能代替 `/v1/setup-readiness` 的完整合同；没有真实网络/实例故障注入。 |
| 启动 API 的权限/资格 | `test_identity_http.py` 的 Viewer 创建 Delivery 返回 403，缺 CSRF 亦 403；`test_project_authorization.py` 的 Editor 无 Project Membership 返回 403、获授权后 202；上述 Setup/onboarding 用例在资格不足时返回 409。 | 202 只表示准入请求被接受，不代表执行完成、候选/Apply；没有对全部角色和资格组合穷举。 |
| 草稿与修复后的动作 | `DeliveriesPage.test.tsx` 预置 localStorage 草稿，模拟首次 Setup GET 503、重新挂载后返回 ready，草稿仍在且确认按钮恢复可用；`LandingRoute.test.tsx` mock 的 ready/blocked 项目分别进入工作台/准备中心。 | 这是 mock 503→ready 与重新挂载，不是实际修复外部资格、按“刷新检查”按钮或真实浏览器持久性验证。 |
| Viewer/Editor/Administrator 与错误状态 | `DeliveriesPage.test.tsx` 验证 Viewer 无创建按钮、默认 Administrator 需先确认边界再 POST；Python API 验证 Viewer 403、Editor 的 Project Membership 约束；页面还覆盖空目标焦点、缺 Pipeline 与准备度错误。 | 未单独测试 Editor 的页面入口；没有同一主路径的真实桌面/移动视口、键盘对话框焦点进出和运行室/Release 角色走查。 |

## 退出边界与未知

本地 5+11 项可证明被选中的只读投影、API 403/409 和组件 fail-closed 合同仍在当前
checkout 上通过，不能证明目标四仓、冻结 Provider/Method、Verification Profile/Qualification、
Source/Index 新鲜度或七个 Knowledge Context。当前工作树尚有未提交文档，且无同一冻结干净
Revision 的正式浏览器、Deterministic/Live Gate 原生零容差报告。故 S2、NR-02、NR-08 均
**未正式退出**；后续应由授权工程/主任务在隔离目标实例上核实时 UI/API、资格 Hash、
角色与桌面/移动/键盘路径，再按阶段计划独立验收。

本切片没有读取账号密码、凭据或知识正文，没有访问旧业务库、8117、真实 Provider/四仓，
没有启动新 Live Delivery、批准 Gate、执行真实 Apply、下载依赖或发起外部操作。
