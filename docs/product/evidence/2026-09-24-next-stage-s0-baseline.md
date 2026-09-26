# 下一阶段 S0 基线与差异对账（2026-09-24）

状态：`S0 静态盘点完成 / 正式退出未通过`；`NR-03 Blocked`。本文件只归档非秘密事实，
不是运行实例、四仓、知识源、业务 Gate 或 Release 的权威记录。

依据：[下一阶段计划](../NEXT-STAGE-DELIVERY-PLAN.md) S0、
[产品验收项](../NEXT-STAGE-DELIVERY-READINESS.md) NR-03、
[架构总览](../../architecture/ARCHITECTURE.md) 的事实优先级。

## Draft Plan → Architecture Review → Revise Plan → Final Plan

Draft Plan：只新增本证据文件；核本机 Git、公开锁、既有隔离 Bundle、非秘密工具环境，
区分当前已证实、历史未复测和未知；不读取运行实例/业务数据库/秘密，不执行外部动作。

| Architecture Review 字段 | 结论 |
| --- | --- |
| Architecture Impact | `None`。这只是文档事实盘点，不改变产品代码或合同。 |
| Findings | ACWM 跨 Stage 与产品 Workcell/Release 权威不变；没有新的模块依赖、数据所有权、状态机、并发/恢复、权限/Workspace、Legacy/Migration 或外部集成策略。静态锁、Manifest 和本机工具回执不能充当运行时资格、七个 Context 或 Live 证据。当前工作树 dirty，旧 Bundle 不得追认为本轮最终候选。 |
| Required Revisions | 将旧基线/旧浏览器结果标为历史；分别列出 Method 锁与实际安装、Provider 模板与 Published Binding、Node 内容检查与四仓 Verification Qualification；给所有未知项具名角色 Owner。 |
| ADR Required | `No`。不跨 ADR 门槛。 |
| Architecture Document Delta | 无；不修改架构总览或既有 ADR。 |
| Outcome | `Approved`，仅批准本事实文档的最小范围，不表示 S0/NR-03、业务 Gate 或 Apply 获批。 |

Revise Plan：加入上述证据级别与授权边界。Final Plan：新增本文件，核命令结果、脱敏内容和
Git diff；不改当前四份未提交文档。Architecture Reconciliation：本文件不改变系统架构或状态。

## 当前已证实（本机静态，不等于运行实例）

| 项目 | 身份与证据 | 不能推导 |
| --- | --- | --- |
| Product 工作树 | `codex/next-stage-delivery-readiness@dbd3bbb651aaeb11693d9f1efb37bad6c3842fa8`；盘点前有三份已修改的 tracked 文档及一份既有未跟踪证据文件，均属他人工作，未覆盖。本机缓存 `origin/main=5395a4bc1a2ed5960c774a7f12bb417f896053a5`。 | 当前是 dirty 工作树；缓存 remote ref 未联网核实，不能称为当前远程 main 或干净交付候选。 |
| Product/ACWM 锁 | `pyproject.toml` SHA-256 `bd9299f6ee6d00f9f31e87dc84646b03051c8a6ec9fe997680e982234182c27c`；`uv.lock` `a00c74ccc9fb63eae27cbee2b49d5af35fd0aafd58d5504123fb6ef7e82585e0`；`framework-lock.json` 锁 ACWM `0.5.1@ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed`。 | 本切片未直接访问 Preview；主任务对本机 dirty source Preview 的观察见下。正式目标身份与 Published Binding 仍未知。 |
| 四个公开 config | `capabilities.yaml` SHA-256 `4ee6cda7cb6469975a9a7ce861cc46186cc085e1dff4c5f8301614925c4e93e7`；`framework-lock.json` `cd9b839e144fef8e351b63fdbfd41a322696a3123521485698a27bc8ca038c90`；`journeys.yaml` `97aaca41e31f0e4b2754afbb69382570baed6b6dfd40bf2113be8ed5a4c2a436`；`method-packs-v050.json` `dcc62e2b1bb148722c5935207f121a6b262f1f899eb618d10bd32d07b7f97a6b`。 | `journeys.yaml` 是模板，不是本轮已发布 Revision；文件 Hash 不证明外部连接可用。 |
| Method Pack 锁 | `method-pack-store-v1`：`bmad-method@6.11.0` 归档 SHA-256 `c0101a6061eb4eacaab24ba8ab8f38a749de1bd50338c5f74e51ee3edc0c0fed`、Qualification Hash `9ab4489da421ff615731c69461f0ea7d4a8c89a7cec47e08e56964f811e0f48c`；`bmad-method-test-architecture-enterprise@1.23.4` 归档 `913cbb7f57ad64504c329fa2a650fda870cf1d794f7323de2fc8920b2080568d`、Qualification `7d6dfa18888bfc577995318592a88aff721a8335fad60c4ca380d03f676116c8`。 | 未检查实际 Method Store/Attempt Snapshot；锁值不能冒充已安装或冻结资格。 |
| 既有隔离 Bundle | `/private/tmp/atos-s1-dbd3bbb.WNwdPm/bundles/agent-team-os-0.5.1` 的 v2 Manifest SHA-256 `4168ba4e60587f4ace14cc191b8508e3e212e577ec38356166e045c3dcf79967`；今日只读 Verifier 返回 `verified`；Manifest 绑定旧 clean `dbd3bbb`、共 99 项（wheel 1、config 4、Console 40、Migration 47、默认 Dataset 5、pyproject/uv.lock 各 1）。 | 当前四文档尚未入新的干净 Revision；旧 Bundle 只作历史局部制品证据。未完成无 checkout 独立安装、登录、Method 安装或三轨。 |
| 本机工具部分身份 | Python 3.11.1 和 3.12.12 均为 arm64，满足 `>=3.11,<3.13`；内存 SQLite 检查中 3.11.1 的 `enable_load_extension` 不可调用、3.12.12 可调用。当前 `.agent-team-os/verification-tools/node_modules` 的只读内容资格检查通过，`verification-node-environment-v1` Hash `e7456a49141b23ea54ce61a54e96423313a0da84ba3b7cf4717b207904eedff9`。 | 3.12 的 callable 不证明实际 sqlite-vec 可加载；Node 部分通过不等于完整工具闭包、四仓 Profile/Qualification 或运行实例选用该路径。 |

主任务另于本日观察到本机 8117 Local Preview 从当前 dirty checkout 启动，隔离临时 Data Root 为
`/private/tmp/atos-preview-20260924.CLxlip`；其 source Build Identity 报告 Product Revision
`dbd3bbb…`、`product_worktree_clean=false`、ACWM `ae46ea81…`、Build Snapshot Hash 前缀
`6113f362…`。主任务报告启动前 8 项 readiness 为 ready、HTTP `/`/静态资源 200、匿名 session
401（预期）、bootstrap/login 成功。本切片未复测该会话；它不是无 checkout 独立 Bundle、正式目标
实例、四仓资格或 NR-03 通过证据，临时路径/会话也不承诺长期可回读。未记录账号或密码。

## 历史未复测与陈述差异

| 来源/旧陈述 | 本轮对账 |
| --- | --- |
| 下一阶段两份原文档以 `main@5395a4b…` 写作 | 这是 2026-09-22 的文档基线；本机当前 HEAD 是 `dbd3bbb…` 且 dirty。不要把历史 baseline 或本机 remote 缓存当本轮正式冻结 Revision。 |
| `V0.5.2-INTERACTION-CLOSURE-ACCEPTANCE.md` 的 dirty 工作树、§6 旧“未观察真实 Codex 闭环” | 该文 Fact 25/§7 随后记录真实 Codex **local-PR** 到 Candidate/Evidence/onboarding ready，四仓 main 未变且无 Remote Apply/Manifest；不是本轮同干净 Revision 的真实四仓 Live Release。 |
| 产品总纲 §1.7/§7.3 的 `needs_attention` 可被通用 Cancel 终结 | 当前代码已有 `tests/test_project_release_recovery.py` 对取消拒绝的公共 API 断言；2026-09-23 本地隔离 S5 三文件回归 21 passed。旧总纲不能无条件代表当前行为；未在真实部分 Apply 场景重验。 |
| 产品总纲 §11.1 的四仓共用 Python `unittest` | 当前 `VerificationProfileCatalog` 与相关测试已有独立 Profile/Qualification 机制；2026-09-23 的 S2 前置三项公共接口回归 3 passed 仅覆盖 Readiness fail-closed。四仓当前资格和工具身份是否新鲜仍未知。 |

解释：S0 已能界定一个 Product/ACWM 静态锁与旧 Bundle 的本机身份；外部治理、运行身份和资格
尚无本轮可复核回执。历史 Deterministic/local-PR、构建通过、现有 8117 Preview 或本机端口均不能
替代 NR-03 的外部证据。

假设（待确认）：目标仍为可信本机 `four-repo-r2-alpha` 的四个专用评测仓库和受批准知识源；
正式版本命名及环境边界由项目负责人决定。`legacy-default` 兼容路由不是目标 Project 指定。

## Unknown、授权范围与 NR-03 下一动作

| NR-03 依赖 | 当前状态 | 下一证据与责任人 |
| --- | --- | --- |
| 正式目标运行实例 Product/ACWM Build Identity、Product/Data Root | `Unknown`；本切片未访问实例或业务库，主任务观察的 8117 dirty source Preview 不是目标身份 | 交付管理员提供目标实例的脱敏只读身份与 Root 回执；工程、主任务对齐最终干净 Revision。 |
| 目标 Project 与四个不同仓库的受控 ID、负责人、基线/权限/分支政策 | `Unknown`；公开锁无本轮实际绑定 | 项目负责人/管理员登记互异资源 ID、各仓 Owner 与可验证的权限/政策回执；仓库 URI/凭据走受控渠道，不写本报告。 |
| Feishu Tenant、Approved Source 与七个 Knowledge Context、权限新鲜度及 Index/Ollama 资格 | `Unknown`；未调用 Provider 或知识状态 | 知识源管理员与交付管理员按单独授权形成 Source/Index/模型的脱敏新鲜回执；旧 Binding 缓存不替代。 |
| 当前 Published Pipeline/Planning Provider、Method Store、四仓 Verification Profile/Qualification | 静态锁已知，实际冻结与资格 `Unknown` | 工程/管理员给出被选 Provider Binding、Method/工具身份、四仓 Qualification Hash 与时间；失效后仅在明确状态写入授权内重资格化。 |
| 本地独立评测 S1 | `Blocked`：在本任务已审计的隔离 wheelhouse/缓存中，3.12/macOS arm64 所需 28 个锁定 PyPI 原始 wheel 缺 28 个；不推断全机其他目录也不存在归档。既有 Git ACWM/Product wheel 不能替代。 | 产品 Agent 本轮**未批准**新的 PyPI GET；保持离线 wheelhouse 入口，待管理员提供可逐件复算锁 Hash 的原始归档及后续独立安装范围裁决。不得用已解压缓存或 `--no-deps` 冒充完成。 |
| 真实执行预算、外部传输/PR、Bundle 的业务 Gate/Apply、正式版本号 | `Unknown/未授权` | 项目负责人定义具体范围和审批人；产品负责人裁决版本/异常；具体 Gate/Apply 另由获权人对冻结对象决策。 |

本 S0 盘点切片获授权的只是本机非秘密盘点和新增本文件；这不概括用户其他独立授权。此切片未获授权读取秘密、刷新真实 Provider、修改运行
实例或业务数据库、访问四仓、下载 PyPI、批准业务 Gate 或 Apply。计划中的 Owner 是角色责任，
不是这些人员已接受任务的证明。S0 仍缺经负责人确认的目标/环境/授权边界，NR-03 保持
`Blocked`，不把 Unknown 自动写成 Ready。

## 本次观察命令与验收界限

- `git rev-parse HEAD`、`git status --short`、`git diff --name-only`、`git branch --show-current`、本机 `git rev-parse --verify refs/remotes/origin/main`：exit 0；不联网。
- `shasum -a 256 pyproject.toml uv.lock config/{capabilities.yaml,framework-lock.json,journeys.yaml,method-packs-v050.json}`：exit 0；Hash 如上。
- `.venv/bin/python -B -c '<读取公开 Method 锁摘要/旧 Bundle Manifest 分组>'`：exit 0；未输出下载 URL 或秘密。
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c 'inspect_dependencies(("node_modules",))'`：exit 0，仅 Node 内容回执；未运行四仓资格。
- `/usr/local/bin/python3.11 -I -B -S -c '<内存 SQLite 能力>'` 与 `/opt/homebrew/bin/python3.12 -I -B -S -c '<内存 SQLite 能力>'`：均 exit 0，未加载扩展。
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/verify_delivery_bundle.py <旧隔离 Bundle>`：exit 0，`verified`；输出明确 Deterministic/Live `not_run`、Apply `not_authorized`。
- `rg`/`sed` 核根规则、架构/ADR、两份阶段文档、历史总纲和公开配置：完成；公开 config/默认 Dataset 没有本次目标 Project/四仓 ID。未读业务库、账户数据或秘密。

未运行新的测试、浏览器、真实 Provider/Delivery、下载/安装、业务 Gate/Apply 或 Release Report。
本文件通过事实对账只降低信息不确定性；正式 S0 与阶段 A/B/C 退出仍需各自合同证据。
