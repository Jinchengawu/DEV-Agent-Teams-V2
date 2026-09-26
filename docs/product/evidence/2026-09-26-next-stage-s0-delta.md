# S0-delta：候选身份与离线可验证性时点证据（2026-09-26）

状态：**局部本地取证，S0/NR-03 未退出**。本文记录隔离 checkout 中未提交的
Method Pack 切片及本轮离线检查；不是新 Build、独立 S1 安装、四仓 Qualification、
CI、Browser/Deterministic/Live Gate、业务批准或 Apply 回执。依据
[下一阶段计划](../NEXT-STAGE-DELIVERY-PLAN.md)、
[产品验收范围](../NEXT-STAGE-DELIVERY-READINESS.md)与
[交接 Runbook](../../runbooks/DELIVERY-HANDOFF-EVIDENCE.md)。

## 计划与授权边界

- Draft Plan：冻结当前 Product/ACWM/锁/未提交 diff 身份，用原生本地检查定位
  S0/S1/S2 可验证与未知边界。
- Architecture Review 六字段：`Architecture Impact=None`；`Findings` 为
  ACWM 跨 Stage、产品 Workcell/Release/Apply 权威不变，临时 SQLite/TestClient
  与离线工具不形成 Live 资格；`Required Revisions` 为分开记录全量首次入场阻断、
  较小模块重跑和外部未知项；`ADR Required=No`；
  `Architecture Document Delta=无`；`Outcome=Approved`，仅针对本证据文档。
- Revised Final Plan：临时计划 SHA-256
  `24529fc1d2f463a9723c662d502a14a99f88001e33446ced2559038cc984dd87`，
  经 PM/架构二次 Review 批准的范围仅为**新增本文这一份文件**。
- Implementation：只写本文；未改原有 20-path diff、主仓四份 Hold、产品代码或数据库。
  Architecture Reconciliation：未改变架构，故不改架构总览或 ADR。

## 身份、来源与时点

以下 `Fact` 为 software_delivery_engineer 本轮原生命令回读，除单独标明者外均来自
2026-09-26 当前隔离 checkout。取证未为每条 pytest 命令另建计时日志；
离线工具 `environment.json` 文件时间为 **2026-09-26 18:33:33 +0800**。

| 分类 | 观察 | 边界 |
| --- | --- | --- |
| Fact：Product | detached `HEAD=60bef27d878ffbac32d0edfb4ba3e03a2f140ef1`；本次写文档前为 16 个 tracked 修改与 4 个 untracked 源码/测试路径 | 不是 clean Build，不继承旧 Bundle 或 CI |
| Fact：ACWM | 独立 clean checkout `HEAD=ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed`；`pyproject.toml` 与 `uv.lock` 均钉此 Revision | 锁与本机 checkout 一致，不证明新候选独立安装 |
| Fact：锁 | `uv.lock` SHA-256 `a00c74ccc9fb63eae27cbee2b49d5af35fd0aafd58d5504123fb6ef7e82585e0`；`config/method-packs-v050.json` SHA-256 `dcc62e2b1bb148722c5935207f121a6b262f1f899eb618d10bd32d07b7f97a6b`；Verification 工具示例 `pnpm-lock.yaml` SHA-256 `1d58b53da5a292e5c4ae2a509bb89ac53d4628431fc9fd6bf51c2db34c90e0e0` | 文件身份不等于 Method 已装或四仓资格 ready |
| Fact：未提交改动 | 写本文前 tracked `git diff --binary` SHA-256 `ca2775aff27105bf8f8dda0aa4a9483542338d09b8c6f2fc5e67cf277b02a61a`；4 个 untracked 文件 Hash 见下文 | Hash 为时点快照，不是 Git Revision/Build Identity |
| 历史/转达 | PM 经主任务转达对原 20-path Method 切片作了局部 `Local Accept`；上一轮九模块曾 `140 passed` | 非本文新 PM 签名、非新 SHA CI、非 S1 完整验收 |

写本文前的 16 个 tracked 修改路径：

```text
docs/architecture/ADR-0014-AGENT-WORKCELL-AUTHORITY.md
docs/architecture/ADR-0020-DELIVERY-BUNDLE-PRODUCT-ROOT.md
docs/architecture/ARCHITECTURE.md
docs/runbooks/DELIVERY-HANDOFF-EVIDENCE.md
scripts/install_method_packs.py
src/agent_team_os/delivery_bundle.py
src/agent_team_os/method_pack_cli.py
src/agent_team_os/modules/extensions/method_packs.py
src/agent_team_os/modules/workcells/stage_driver.py
src/agent_team_os/preview.py
src/agent_team_os/release.py
tests/test_delivery_bundle.py
tests/test_preview_startup.py
tests/test_release_method_fixture.py
tests/test_runtime_extensions.py
tests/test_workcell_stage_driver.py
```

写本文前的 4 个 untracked 文件及 SHA-256：

```text
f330b5052b55ad8c9e7412f59f214fee4ea103a0afb87071f757c03f8a072e4e  src/agent_team_os/infrastructure/method_pack_registry.py
756245dc1dd9acff47336573d4c2e0e58fb3d201e90c28d15ee7f3c6eb1f6654  tests/test_gate_app_startup.py
6fff0a3fd949eb4d0538efac074a5eb754946eab84ba8869420a06a65ecd3c35  tests/test_method_pack_install_cli.py
a51c2f87fb0af0e5d606b7128e937ef93070c45bd38e5477bdab2c2de9d753c8  tests/test_method_pack_registry.py
```

## 原生本地验证

下列命令只脱敏**个人绝对路径**：`<CONTROL_CHECKOUT>` 指借用 Python 3.12 venv
与既有 frontend `node_modules` 的控制仓；`<PRODUCT_SRC>` 为本隔离 Product 的
`src`；`<ACWM_SRC>` 为上述 clean ACWM 的 `src`；`<TOOLS_ROOT>` 精确指
`/private/tmp/atos-verification-offline.DXSeic/tools`。占位符不是原样可执行命令；
其余环境变量、选项、测试集合与 exit 按本轮原生调用列出。`env -i` 未继承
Provider 凭据，真实 Codex 集成用例未设置 `AGENT_TEAM_OS_LIVE_CODEX=1`。

| 动作与证据来源 | 脱敏原生命令形态 | 原生结果及限度 |
| --- | --- | --- |
| SDE：导入来源 | `env -i PATH=/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -c 'import sys, agent_team_os, acwm, pytest, playwright, sqlite_vec; print(sys.version.split()[0]); print(agent_team_os.__file__); print(acwm.__file__); print(pytest.__version__)'` | exit 0；Python 3.12.12；Product/ACWM `__file__` 分别来自两处隔离源码；pytest 8.4.2 |
| SDE：全量收集 | `env -i PATH=/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -m pytest --collect-only -q -p no:cacheprovider` | exit 0；941 tests collected；收集不等于执行通过 |
| SDE：首次全量 | `env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -m pytest -q -p no:cacheprovider` | exit 1；`917 passed, 1 skipped, 5 failed, 18 errors in 188.36s`。18 个 error 与 3 个 fullstack failure 的所见首帧/入场阻断为缺 `verification-tools/environment.json`；**不能由首帧推断全部最终根因**。另 2 个 QA runner failure 所见 `socket.bind(127.0.0.1,0)` 为 sandbox `EPERM` |
| SDE：离线工具准备 | `env -i PATH=/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B scripts/prepare_verification_tools.py --source-node-modules <CONTROL_CHECKOUT>/examples/health-contract-v1/frontend/node_modules --target <TOOLS_ROOT>` | exit 0；仅在新私有临时根下复制，未调用包安装或网络；`environment.json` SHA-256 `0b957baea696cb6cb812395a7bad2bba5bfc72013193081dfdf1f791cdbaac02`，文件时间见上 |
| SDE：工具内容资格 | `env -i PATH=/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR=<TOOLS_ROOT> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -c 'from agent_team_os.infrastructure.verification.tool_environment import inspect_dependencies; result=inspect_dependencies(("node_modules",)); print(result[0].name, result[0].version, result[0].content_sha256)'` | exit 0；`node_modules` 内容 SHA-256 `d9793b37fe42334449bee1d0875a748b10f905e98b8c7b5456e3a0d2e59f6e02` 与 receipt 一致；仅是复制后内容检查 |
| SDE：较小四模块重跑 | `env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR=<TOOLS_ROOT> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -m pytest -q -p no:cacheprovider --tb=line tests/test_fullstack_verification.py tests/test_fullstack_verification_api.py tests/test_fullstack_workcell_pipeline_e2e.py tests/test_release_verification_sources.py` | exit 1；`6 passed, 1 failed, 18 errors in 30.20s`。这**不是**941 项全量一一重跑；该较小集合内缺 receipt 首帧消失，18 setup errors 的所见阻断转为 backend 回环 bind `EPERM`；1 个 pipeline failure 为 `MACHINE_VERIFICATION_FAILED`，底层原因仍 Unknown |
| SDE：S2 局部公共合同 | `env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=<PRODUCT_SRC>:<ACWM_SRC> AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR=<TOOLS_ROOT> PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <CONTROL_CHECKOUT>/.venv/bin/python -B -m pytest -q -p no:cacheprovider --tb=short tests/test_interaction_closure.py tests/test_preview_startup.py tests/test_gate_app_startup.py` | exit 0；`30 passed in 7.01s`，仅临时 SQLite/TestClient 与公开合同，不含真实外部资格、浏览器 UI 或运行实例 |

静态检查（不是新测试结果）：[CI workflow](../../../.github/workflows/ci.yml)
在全量 Pytest 前明确执行 `prepare_verification_tools.py` 并设置
`AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR`；
[工具实现](../../../src/agent_team_os/infrastructure/verification/tool_environment.py)
核四个包的版本和复制内容 Hash。本轮源输入是**控制仓既有** `node_modules`，
不是在本隔离 checkout 以 lock 新安装的原始闭包；`inspect_dependencies` exit 0
**不证明**源 provenance、四仓 Verification Qualification、Method Pack 来源或 S1
独立安装。主任务此前对旧远端 CI 的只读诊断/复核不计入上述 SDE 原生测试结果。

## 事实解释、未知及下一责任

| 类型 | 结论 | Owner / 解除证据 |
| --- | --- | --- |
| Fact | 本文取证时隔离 Product 为未提交 20-path diff，clean ACWM 与锁身份如上；30 项本地公共合同通过 | 工程保留差异；PM 仅能局部审查，不把本文件签成正式候选 |
| 解释 | 首次缺工具 receipt 是当时进入部分验证用例的阻断；工具准备后较小重跑集出现回环 `EPERM`。它们分别是环境/权限证据，不足以证明所有业务断言都通过或失败 | 工程如需继续全量，须先有受审的仅 loopback 环境权限与同源离线工具资格；不通过放宽断言解决 |
| 假设 | 控制仓既有 Node 目录可作为本地诊断输入；未将它当作独立锁定来源 | 工程/CI Owner 用当前 lock 的干净安装和内容回执核实后才可提升资格 |
| Unknown | Product 20-path diff 的新 clean SHA、相应 CI、Bundle/Build Identity、官方 Method 安装、独立评测账号与无 checkout 登录均未发生 | 工程提交/构建/安装须另授权；S1/NR-01 保持未退出 |
| Unknown | 目标 Project/四仓、Verification Profile/Qualification、Feishu Source 权限与知识新鲜度、冻结 Provider/Method 和当前实例身份 | 管理员提供脱敏新鲜回执；PM 判 S0/NR-03 与 S2 的每项状态。当前仍未退出、不写 ready |
| Unknown | 回环可用环境下四模块重跑及完整 941 项全量结果；pipeline `MACHINE_VERIFICATION_FAILED` 底层原因 | 工程仅在精确授权的隔离环境重验并给首帧/原生 exit；若为真实代码失败再起 Draft/Architecture Review |
| 未授权/未执行 | 真实 Registry/Provider、业务 DB、四仓外写、Gate、Apply、Release 三轨 | 由对应获权 Owner 对具体对象另作批准；本文件不替代任何决策 |

本轮未访问真实 Registry/Provider/Feishu、旧实例或业务数据库；未执行 PR、
commit/push、业务 Gate、Apply 或正式 Release。`git diff --check` 与本文
新增后的精确 Git 状态须在交付 PM 内容验收前再次回读；尚无正式同 Revision
Browser、Deterministic、Live `FAIL=0/WARN=0/skipped=0` 三轨证据。
