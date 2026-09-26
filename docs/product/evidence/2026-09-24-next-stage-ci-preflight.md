# 下一阶段本地 CI 对齐预验证（2026-09-24）

状态：**部分 Local Accept，仅限本文件列明的本地检查**。这是当前 checkout 的工程诊断，
不是 GitHub CI required check、Browser/Deterministic/Live Gate、正式 Release Report、
业务 Gate 或 Apply 证据。依据为 [CI workflow](../../../.github/workflows/ci.yml)、
[交付计划](../NEXT-STAGE-DELIVERY-PLAN.md)与[验收合同](../NEXT-STAGE-DELIVERY-READINESS.md)。

## Draft Plan → Architecture Review → Revise Plan → Final Plan

Draft Plan：只新增本文件，归档已经执行的本地 CI 对齐命令、原生输出、环境差异和未运行项；
不重跑失败项、不覆盖 `console/dist` 或生成的 OpenAPI 文件，不更改任何运行状态。

| Architecture Review 字段 | 结论 |
| --- | --- |
| Architecture Impact | `None`：只归档工程观察，不改变产品或测试合同。 |
| Findings | ACWM 的跨 Stage 权威与 Agent-Team-OS 的 Workcell、权限、Verification、Release/Apply 权威均不变。没有新增 Module/Port/Adapter 依赖、数据所有权或状态机、并发/恢复、Workspace 隔离、Legacy/Migration 或外部集成策略。测试的本地端口 `EPERM` 是环境限制；Console 进程 exit 137 原因未定。两者都不能冒充产品结论。当前 dirty checkout 和不同 Python/Node 版本也不能代替同干净 Revision 的原生 Gate。 |
| Required Revisions | 将通过的局部检查、pytest `-x` 的部分执行、Console 无摘要退出、Build 未运行分开记载；临时目录生成的文件仅称本地等价字节核对，不称 CI 原位命令。 |
| ADR Required | `No`：未跨越权威、依赖、持久化、安全或 Release/Apply 决策门槛。 |
| Architecture Document Delta | 无；不编辑架构总览或 ADR。 |
| Outcome | `Approved`，仅针对新增本证据文件，不批准正式 CI、Gate 或 Apply。 |

Revise Plan：采用上述证据分层。Final Plan：仅新增本文件，记录可复核命令、退出码、
Hash 与下一隔离 runner 条件，再做脱敏、whitespace、Git 状态和文件 Hash 回读。
Implementation：仅写本文件。Architecture Reconciliation：无产品架构或运行状态变化。

## 身份、环境及 CI 原合同

- 本地检查的 `HEAD=dbd3bbb651aaeb11693d9f1efb37bad6c3842fa8`。运行前后原有
  `docs/architecture/ADR-0018-KNOWLEDGE-INDEX-DELIVERY-CONTEXT.md`、
  `docs/architecture/ARCHITECTURE.md`、`docs/product/NEXT-STAGE-IMPLEMENTATION.md`
  均为修改状态，`docs/product/evidence/` 已有未跟踪证据文件。本切片未覆盖这些内容。
- 本机 `.venv/bin/python` 为 `3.12.12`，Node 为 `v22.17.1`，pnpm 为 `10.13.1`；
  CI workflow（文件 SHA-256 `07a8db1f3cb9a12f7e934cdeca84847f7258adf84bff7c7dcb5f8017b8cd017f`）
  使用 Ubuntu、Python `3.11`、Node `24`、pnpm `10.13.1`，先执行 `uv sync --extra dev --extra live --locked`、
  两组 frozen pnpm install、隔离验证工具准备和 Playwright Chromium 安装，再执行 Dataset、
  fresh Migration、Ruff、Mypy、全量 pytest、`uv build`、OpenAPI 原位生成/check、Console
  typecheck/test/build。本轮没有运行安装、下载、Chromium 安装或 GitHub CI。
- 测试命令使用清空继承环境的 `env -i`，只显式传入下表前缀字段；临时根为
  `/private/tmp/atos-ci-local-dbd3bbb.5687tt`，创建时权限为 `0700`。它只用于本轮缓存、
  pytest 临时文件、全新 Migration SQLite、OpenAPI 与 TypeScript 临时输出，不是旧业务 Data Root。
  未设置 Live Codex 开关，也未调用真实 Provider、四仓远端或运行实例。

下表中 `PY_ENV`/`NODE_ENV` 是**命令展示缩写**，不是额外执行过的脚本：每行实际命令均以对应完整
`env -i` 参数开头。`PY_ENV` 为
`env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt XDG_CACHE_HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt/cache TMPDIR=/private/tmp/atos-ci-local-dbd3bbb.5687tt PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1`；
`NODE_ENV` 为
`env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt XDG_CACHE_HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt/cache TMPDIR=/private/tmp/atos-ci-local-dbd3bbb.5687tt CI=1`。

## 已执行命令和原生结果

| 检查 | 实际命令（以上述前缀展开） | Exit / 观察 |
| --- | --- | --- |
| Ruff | `PY_ENV .venv/bin/ruff check .` | `0`；`All checks passed!`。 |
| Mypy | `PY_ENV MYPY_CACHE_DIR=/private/tmp/atos-ci-local-dbd3bbb.5687tt/mypy-cache .venv/bin/mypy`（`MYPY_CACHE_DIR` 作为 `env -i` 字段传入） | `0`；`Success: no issues found in 205 source files`。 |
| Dataset | `PY_ENV .venv/bin/agent-team-os-dev eval validate-dataset` | `0`；`status=valid`、`suite_version=1.3.0`、`case_count=10`、`official=false`；`source_sha256=047ebcecfd100e2802a20b3334ef3859b46c5a52cac47843bedd146907d9b5e7`。结构校验不是语义/Live 评测。 |
| Fresh Migration | `PY_ENV MIGRATION_CHECK_DB=/private/tmp/atos-ci-local-dbd3bbb.5687tt/migration-check.sqlite .venv/bin/python -B -c "import os; from pathlib import Path; from agent_team_os.infrastructure.database import MigrationRunner; MigrationRunner(Path(os.environ['MIGRATION_CHECK_DB']), Path('migrations')).migrate()"`（`MIGRATION_CHECK_DB` 作为 `env -i` 字段传入） | `0`；无 stdout；仅新临时 SQLite。 |
| Python pytest | `PY_ENV .venv/bin/python -B -m pytest -p no:cacheprovider -q -x` | `1`；`295 passed, 1 skipped, 1 error in 48.09s`。首个错误是 `tests/test_fullstack_verification.py::test_real_four_repository_design_typescript_http_and_browser` 的 module fixture：临时本地 HTTP 服务 `socket.bind` 返回 `PermissionError: [Errno 1] Operation not permitted`。按 `-x` 停止；**只执行了部分套件**，不是全量 pytest 通过或产品断言失败。未提权、未重试。 |
| OpenAPI 临时导出 | `PY_ENV .venv/bin/python -B scripts/export_openapi.py --output /private/tmp/atos-ci-local-dbd3bbb.5687tt/openapi.json` | `0`；临时文件与 `console/openapi.json` 的 `cmp -s` 为 `0`，双方 SHA-256 均为 `c973716635f37902af434f8c94694eb2850b1c569132576e1204a4a8d4e9c224`。 |
| TypeScript 合同临时生成 | `env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt XDG_CACHE_HOME=/private/tmp/atos-ci-local-dbd3bbb.5687tt/cache TMPDIR=/private/tmp/atos-ci-local-dbd3bbb.5687tt NODE_OPTIONS=--no-warnings console/node_modules/.bin/openapi-typescript /private/tmp/atos-ci-local-dbd3bbb.5687tt/openapi.json -o /private/tmp/atos-ci-local-dbd3bbb.5687tt/schema.ts` | `0`；临时文件与 `console/src/shared/api/generated/schema.ts` 的 `cmp -s` 为 `0`，双方 SHA-256 均为 `be86dee9d92f15e35b99f19cf7b4e99688e3a67e84efbbf01206503359c49dc1`。这不是会原位写文件的 CI `api:check`。 |
| Console typecheck | `NODE_ENV pnpm --dir console typecheck` | `137`；仅有 `tsc --noEmit` 启动行，无检查摘要；原因未定，不标产品缺陷，也不标通过，未重试。 |
| Console test | `NODE_ENV pnpm --dir console test` | `137`；仅有 `vitest run --reporter=verbose --pool=forks --poolOptions.forks.singleFork=true` 启动行，无测试数/摘要；原因未定，未重试。 |

PM 经主任务转达的 **部分 Local Accept** 只适用于有 `exit 0` 原生结果的 Ruff、Mypy、
Dataset 结构校验、fresh Migration 和 OpenAPI/类型临时字节核对；不包含上述 pytest、
Console 命令、Build、正式 CI、S1/S3/S5 退出或 NR-01/07 的正式验收。pytest 的
`1 skipped` 不能证明 Release Report 的零容差；该报告本轮并未生成。

## 未运行、未知与下一隔离 runner 条件

| 项目 | 本轮状态和原因 |
| --- | --- |
| `uv build` | **Not Run**。现有 `.venv` 中 `import hatchling` 返回 `ModuleNotFoundError`（探测组合命令 exit 1）；本轮不下载/安装构建依赖，也不以不同依赖来源生成制品。 |
| `pnpm --dir console api:check` | **Not Run**。该脚本会原位生成 `console/src/shared/api/generated/schema.ts` 并检查 Git diff；本轮改用临时输出逐字节比较，不将其冒称原命令。 |
| `pnpm --dir console build` | **Not Run**。Vite 配置输出 `console/dist`，该目录已存在；typecheck/test 均 exit 137 且原因未定，故不冒险清理/覆盖现有产物或继续触发资源终止。 |
| 完整 pytest、浏览器及 Gate | **Not Run / 未完成**。`-x` 已在本地回环绑定 `EPERM` 时停止；正式 Browser、Deterministic、Live 三轨及零容差 Report 均未执行。 |

下一次应在**干净、冻结的同一 Revision** 的隔离 runner 上，提供锁定的 Python 3.11、Node 24、
pnpm 10.13.1、Python/Console/四仓工具依赖和匹配 Chromium；给测试专用 Data Root、允许测试 fixture
所需的本机回环绑定，并给 Node 检查可观察的进程退出/资源诊断。按 workflow 原顺序运行所有
未完成步骤，保留原生 exit、pass/fail/skip 与制品 Hash。不要将权限/资源诊断等同于产品修复；
如出现可复现代码失败，再按单独 Draft → Architecture Review → Revise Plan → Final Plan
→ Implementation → Architecture Reconciliation 修最小切片。

本轮未读取秘密、未操作 8117 或旧业务数据库、未访问真实 Provider/四仓、未下载依赖，
未 commit/push/PR，未批准 Gate 或执行 Apply。当前结论仍为 `locally_verified` 的**局部工程证据**，
正式 CI、S1/S3/S5 和产品交付目标均未退出。
