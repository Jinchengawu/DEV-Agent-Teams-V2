# 下一阶段 S1/S5 本地回归与 S3 环境预验（2026-09-24）

状态：`局部 Local Preflight`。本文件归档当前 checkout 的测试输出和一次受限环境诊断；
不是 `core-browser-run-receipt-v1`、`GateReport`、正式 Release Report、业务 Gate 或 Apply 回执。
依据：[交付计划](../NEXT-STAGE-DELIVERY-PLAN.md) S1/S3/S5 与
[验收合同](../NEXT-STAGE-DELIVERY-READINESS.md) NR-01/06/07。

## Draft Plan → Architecture Review → Revise Plan → Final Plan

Draft Plan：只新增本文件，记录精确测试命令、退出码、观察到的输出、环境阻断和未运行项；
保留其他 Agent 已修改或新增的文件，不重跑 Gate、不补齐缺失证据。

| Architecture Review 字段 | 结论 |
| --- | --- |
| Architecture Impact | `None`。仅归档本地观察，不改变产品运行或合同。 |
| Findings | ACWM 跨 Stage、产品 Workcell/Release/Apply、Provider/Method 的权威不变；没有新增 Module/Port/Adapter 依赖、数据或状态事实源、并发/恢复、权限/Workspace、Legacy/Migration 或外部集成策略。`tmp_path` 模拟 Apply 不等于真实远端 Apply；端口 `EPERM` 是环境权限失败，不是产品测试失败。当前工作树 dirty，不能将局部测试追认为同干净 Revision 的正式三轨。 |
| Required Revisions | 分开记录 PM 转达的 17/18 项子集 `Local Accept`、A 在启动前失败、B 仅完成 clone/import/锁前置核查；所有正式阶段与 NR 仍未退出。 |
| ADR Required | `No`；未跨架构决策门槛。 |
| Architecture Document Delta | 无；不编辑架构总览或 ADR。 |
| Outcome | `Approved`，仅针对新增本证据文件，不批准 Gate、Apply 或验收晋级。 |

Revise Plan：采用上表证据边界。Final Plan：新增本文件并做内容、脱敏、Git 状态及 Hash 回读。
Implementation：仅写本文件。Architecture Reconciliation：没有产品架构或运行状态变化。

## 身份与证据级别

- 测试时 `HEAD=dbd3bbb651aaeb11693d9f1efb37bad6c3842fa8`；S1 与 S5 各自在运行前后
  回读该 HEAD，值未变。工作树原有 `docs/architecture/ADR-0018-KNOWLEDGE-INDEX-DELIVERY-CONTEXT.md`、
  `docs/architecture/ARCHITECTURE.md`、`docs/product/NEXT-STAGE-IMPLEMENTATION.md` 已修改，
  `docs/product/evidence/` 已有他人/既有未跟踪文件；本切片没有覆盖这些内容。
- PM 裁决经主任务转达：下述 S1 的 17 项和 S5 的 18 项仅为子集 `Local Accept`；这不是
  对正式 S1/S5、NR-01/06、Browser/Deterministic/Live Gate 或发布对象的批准。本文件不是
  PM 签名或原生 Gate 报告。
- 两组 pytest 使用当前 checkout 的 `.venv` 与源码、公开测试资源。S5 中的 Apply 是
  `tmp_path` 临时 SQLite、本地 bare Git 与 `FakeForwardRemote` 的 Deterministic fixture；
  没有真实 Provider、四仓远端、业务数据库或运行实例参与。

## 已执行命令与结果

下表 S3 B 的命令是当时执行记录的脱敏展示：`<ISOLATED_S1_ROOT>` 代指当时的私有临时根，
`<CONTROL_CHECKOUT>` 代指当时的控制仓 checkout。占位符不是原样可执行命令；仅替换路径展示，
不改变当时 Git、Python 导入、退出码或环境阻断的观察结果。

| 切片 | 命令/观察 | Exit 与原生输出摘要 | 可证明/不可证明 |
| --- | --- | --- | --- |
| S1/NR-01 负例自动化 | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/test_delivery_bundle.py tests/test_product_root.py tests/test_bundle_build_identity.py` | `exit 0`；`17 passed in 1.39s`。前后 `git rev-parse HEAD`/`git status --short` 为上述同一 HEAD/原有 dirty 集合；`git diff --check` 为 `exit 0`。 | 覆盖显式错误 Product Root 不回退、缺资源、Manifest/资源篡改、内部 symlink、dirty/旧 schema、代码与 wheel 身份不匹配。未执行无 checkout 独立安装、锁定依赖闭包、Method Pack 安装、独立账号登录或新 Bundle 正式评测。 |
| S5/NR-06 故障恢复自动化 | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/test_external_forward_release_v2.py tests/test_project_release_recovery.py` | `exit 0`；`18 passed in 30.91s`。前后 HEAD/dirty 集合未变；`git diff --check` 为 `exit 0`。 | 覆盖部分 Apply、原 Bundle 恢复、回读/Receipt 丢失、第三 SHA 拒绝、finalization/ack 丢失、Lease 重建及取消/新交付拒绝；只是临时 fixture，不是实际四仓故障演练或原生 S5 报告。 |
| S3 A 环境预验 | `env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'` 的一次内存编排；核心首个受限操作为 `socket.bind(('127.0.0.1', 0))`。 | `exit 1`；`PermissionError: [Errno 1] Operation not permitted`。仅创建一个新 0700 临时根；失败发生在端口选择和子进程启动前。 | **环境权限阻断**，不是浏览器/产品断言失败。`gate_app` 与浏览器均未启动，随机测试账号未生成，未执行 `--gate-c`，无 Receipt、state、截图或 Gate 报告。产品 Agent 随后不批准 sandbox escalation，同一 A 未重试。 |
| S3 B 只读前置 | 隔离 clone 上 `git -C <ISOLATED_S1_ROOT>/source rev-parse HEAD`、`git -C <ISOLATED_S1_ROOT>/source symbolic-ref -q HEAD || true`、`git -C <ISOLATED_S1_ROOT>/source status --porcelain`；`env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=<ISOLATED_S1_ROOT>/source/src PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -c 'import agent_team_os; from agent_team_os.readiness import imported_acwm_revision; ...'`。 | Git 回读为 clean detached `dbd3bbb651aaeb11693d9f1efb37bad6c3842fa8`；私有父根 `0700`。`symbolic-ref -q` 无输出，因 `|| true` 组合命令 `exit 0`；Python `exit 0`，`agent_team_os.__file__` 落该 clone 的 `src`，导入 ACWM Revision `ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed` 与 `uv.lock` 相符。 | 仅证明 B 的部分静态入口条件；未创建 B Data Root，未运行 `python -m agent_team_os.preview gate`。Deterministic 路径在 `src/agent_team_os/release.py` 调用浏览器 Gate，并需本机回环 `socket.bind`；A 已遭该沙箱拒绝且无提权许可，故依“需额外权限即停”停止。不是 B 失败报告。 |

S3 A 的内存编排在 `socket.bind` 处提前异常，后续拟执行的
`uvicorn agent_team_os.gate_app:app` 与
`scripts/browser_feishu_knowledge_e2e.py --gate-c` 均**未执行**。预先静态核查中，
`gate_app` 的 Tenant Resolver/Embedding 为 Deterministic 实现，`AGENT_TEAM_OS_GATE_CODEX_RUNTIME=0`
会选择 Deterministic Planning/Workcell；当前 checkout 的 Console `dist/index.html`、Playwright、
Uvicorn 与 Chromium 文件存在。静态接线、资源存在和 import 检查不能替代成功启动或浏览器运行。

S5 覆盖细分：`test_partial_apply_never_rolls_back_and_same_bundle_resume_forward_recovers`
验证部分 Receipt、`needs_attention`/`release_drifted`/Lease 与完成后 Manifest；
`test_resume_recovers_exact_candidate_when_push_succeeded_before_receipt` 验证精确 Candidate 的
`recovered=true` Receipt 且 FakeRemote 不重复 Push；
`test_resume_forward_refuses_drift_without_rewriting_bundle` 拒绝无 Receipt 仓的第三 SHA。
`test_finalization_failure_keeps_recovery_lease_and_resumes_without_reapplying` 与
`test_committed_finalization_with_lost_acknowledgement_stays_completed` 分别覆盖未提交的恢复和
已提交成功时不降级；`test_project_release_recovery.py` 的公开 API/重启/Guard 用例覆盖取消与
新交付拒绝、Lease 重建、单恢复所有者和同库约束。没有对真实 GitHub main 注入故障。

## 阶段结论、未知与下一授权

| 项目 | 当前判定 | 缺口/下一事件 |
| --- | --- | --- |
| S1 / NR-01 | **未退出**；17 项仅局部自动化通过。 | 锁定原始依赖、无 checkout 独立安装、指定 Product Root、独立账号安全交付及负例须在新干净候选上重验；本轮未获新的 PyPI GET 授权。 |
| S3 / NR-07 前两轨 | **未运行/未退出**；A 为环境 `EPERM`，B 未运行。 | 需产品另行批准本机回环能力，或由具备该权限的隔离 runner 在冻结干净 Revision 上运行严格 Browser Receipt 与 Deterministic Gate；不得把本文件当零容差原生报告。 |
| S5 / NR-06 | **未退出**；18 项仅 Deterministic fixture 回归通过。 | 仍需同冻结 Revision 的隔离故障原生记录；若真实发布遇故障，另需获权管理员按原 Bundle 恢复并回读 Receipt、Manifest、Health/Lease。不得为造证据破坏未授权远端。 |
| 正式 Live/Apply | 未执行、未授权本切片。 | 真实 Candidate/PR、业务决策、Apply 与事后只读 Live 验收各需独立范围和原生证据；本文件不提升任何 Gate 状态。 |

未读取秘密或旧运行库，未调用真实 Provider/Feishu/Ollama/GitHub、未启动第二条 Live Delivery，
未下载依赖，未 commit/push/PR、未批准业务 Gate 或执行真实 Apply。A 的端口限制只说明当前
sandbox 权限；不推断产品在具备回环权限的环境中会通过或失败。
