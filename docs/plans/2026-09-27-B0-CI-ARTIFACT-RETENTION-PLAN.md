# B0 CI Artifact 留存与同 run 回读：计划及收敛记录

状态：仅本地 verifier prototype；工作流上传接线未获权限审查放行，未发生真实 Artifact 上传或回读。
基线：隔离候选 `f26ae975965773a5572a753b801c2e90e2e1f820`，工作树包含其他人的未提交工作。

## Draft Plan

在 `quality` 的 B0 静态审计成功后，计划仅于可信仓库 push 上传唯一受审 Delivery Bundle，
保留三天；同一 run 以 action 输出的 ID/digest 经 GitHub REST 回读 metadata 和原始 ZIP，
独立验证 run/head/name/ZIP SHA、安全解包及 Bundle Manifest/内部 Hash。PR 不上传；任何失败
使 `quality` 失败。普通产品运行态、ACWM、Release/Apply 不变。

## Architecture Review #1：Draft

| 字段 | 结论 |
| --- | --- |
| Architecture Impact | `Cross-boundary`：GitHub Artifact 是新增外部分发与信任边界。 |
| Findings | 当前 B0 只有静态审计；本地合成 ZIP 不证明 GitHub 留存。上传范围、秘密、302 token 转发、ZIP/Manifest 身份混淆、PR 来源与公开可见性均需约束。 |
| Required Revisions | 限可信 push、唯一重新审计的 allow-list Bundle、最小 `actions:read/contents:read`、固定 action 版本、三天留存、缺文件报错、同 run REST 双身份和失败关闭；不记录 token/签名 URL。 |
| ADR Required | `Yes`，修订 ADR-0020 的外部分发边界，不新增重复 ADR。 |
| Architecture Document Delta | 增 `ARCH-20260927-02` 于 `Accepted/Not Implemented`；真实接线及远端回读前不得晋升。 |
| Outcome | `Revise`。 |

## Revise Plan → Final Plan

实施顺序：先用公共 CI 合同与模拟 HTTP/ZIP 测试锁定失败路径；复用现有未跟踪
`scripts/verify_ci_artifact_archive.py`、`tests/test_ci_artifact_archive.py`，补上传前复审和
同 run REST 原始 ZIP 独立校验；在明确准许外部留存后才改 `.github/workflows/ci.yml`；
最后进行 Runbook/ADR/架构对账。独立 ZIP SHA 与 Manifest SHA 各守其对象。

本轮权限审查拒绝了会使未来 push 自动上传 Bundle 的工作流编辑。因此 Final Plan 缩为：
仅保留离线 verifier、mock 测试与准确文档；工作流不改，不改道上传，不提交/推送/触发 CI。
公开仓库事实不等于具体 Artifact 可见性或费用已知；本地测试不等于远端身份回读。

## Architecture Review #2：缩界后

| 字段 | 结论 |
| --- | --- |
| Architecture Impact | `Cross-boundary` 设计仍在，但本轮代码只实现离线校验器，不启用外部分发。 |
| Findings | ACWM、Product Runtime、Release/Apply、Schema 均不变；工作流留存权限阻断必须显式保留。 |
| Required Revisions | Runbook 分开已成功的远端 B0 静态审计与未接线 Artifact；架构条目移至 `12.1 Accepted/Not Implemented`；诊断 Python 3.11 依赖缺口及完整 Mypy 现存错误。 |
| ADR Required | `Yes`，ADR-0020 仅记录待接线决策及证据边界。 |
| Architecture Document Delta | `ARCH-20260927-02` 维持 `Accepted/Not Implemented`；不预写 `Implemented/Verified`。 |
| Outcome | `Approved` 仅对离线 verifier/测试/文档收敛；工作流接线为 `Blocked`，须重新取得授权并复核。 |

## Architecture Reconciliation 与未决验收

离线脚本能够拒绝错误身份、危险 ZIP/Location、超限内容和 token 转发；它不证明 GitHub
服务端存储、REST 302 实际主机、Action digest 语义或费用。主任务委派 Agent 的只读 GitHub
API 回执显示，[run 36302936929](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/actions/runs/36302936929)
与 [quality job](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/actions/runs/36302936929/job/108573912485)
在 `f26ae975…` 上成功，第 21 步 B0 静态审计成功，Artifact `total_count=0`；PM 接受
这一分类，本子任务未独立查询远端。仓库 API `visibility=public`、`private=false`，但不
证明未来 Artifact 具体可见性或费用。未来接线后应在同 run
记录 action 输出、REST metadata、原始 ZIP SHA 和 Bundle 内部校验，再单独做 S1 与
Browser/Deterministic/Live Release Gate；不得把 B0 结果换算为这些验收。

## 本地验证环境诊断（非独立交付验收）

- 候选可用 `python3=3.11.1`，其 `importlib.util.find_spec("acwm")` 为 `None`；故原生
  `PYTHONPATH=src python3 -m pytest` 在收集时缺 `acwm`，不是行为断言失败。未安装依赖。
- 主工作树锁定环境的解释器为 `3.12.12`，`acwm` 来自该环境；设置候选
  `PYTHONPATH=src` 后，`agent_team_os.__file__` 指向本隔离候选源码。该混合环境可用于
  离线 mock 焦点验证，但不能替代候选 Python 3.11 独立安装。
- Python 3.11 `ast.parse(feature_version=(3,11))` 对新 verifier/测试两文件通过，仅证明语法。
- 对新脚本指定路径运行严格 Mypy 时，会跟随导入既有
  `scripts/audit_ci_bundle_artifact.py`，其 `_reject() -> None` 虽总抛异常，却令 Mypy 在
  `audit_bundle:161` 与 `confirm_expected_skip:263` 报 `Missing return statement`。
  `mypy --follow-imports=skip scripts/verify_ci_artifact_archive.py` 通过；仓库 CI 同形的
  不带路径 `mypy` 按 `pyproject.toml packages=["agent_team_os"]` 检查 206 个源码文件并通过。
  这不代表外部 Artifact 路径已被 CI 类型检查，也未修改既有审计脚本。

## 2026-09-27 后续增量：离线 verifier 聚焦回归

本节仅追加获准的本地 mock/ZIP/HTTP 复测，不覆写上文工作流被权限审查拒绝的事实。
隔离候选仍没有 `.github/workflows/ci.yml` 的上传接线；没有真实 GitHub 请求、Artifact
上传或远端原始 ZIP 回读。使用主工作树 Python `3.12.12` 环境加候选 `PYTHONPATH=src`，
属于混合环境局部验证，不是候选 Python 3.11 独立安装。原生命令：

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 '/Users/zhuizhui/网盘同步/work/学习/AI/DEV-Agent-Teams-V2/.venv/bin/python' -B -m pytest -q -p no:cacheprovider tests/test_ci_artifact_archive.py
```

exit `0`，`24 passed in 1.59s`。测试只用本地合成 Bundle/ZIP 和 monkeypatch HTTP：
正例核 ZIP SHA 与 Manifest/内部资源，负例涵盖 action/REST digest、run/head/name
身份失配，路径逃逸、ZIP symlink、超限展开、Manifest 内容漂移、上传前 receipt/隐藏文件
失配、危险 302 Location、同 run metadata 错误，以及 token 只到 API、不随签名 URL
转发。该 24 例**没有**证明真实 GitHub REST 响应、ZIP 字节与 action digest
一致、服务端留存、Artifact 可见性或费用。

`/bin/df -k /private/tmp` 各次 exit `0`：上述 pytest 前后 `Available`
分别为 `11,534,152` 与 `11,512,048 KiB`，始终高于
`10 GiB + 576 MiB = 11,075,584 KiB` 本轮保底线。另用主工作树解释器执行
`-m ruff check --no-cache scripts/verify_ci_artifact_archive.py
tests/test_ci_artifact_archive.py`，单独命令 exit `0`、`All checks passed!`；
系统 Python 3.11.1 `ast.parse(feature_version=(3,11))` 对这两文件输出
`ast311=passed 2`，仅为语法检查。没有安装/同步依赖、读取秘密或运行 CI。

### 本次事实增量的 Plan 与架构对账

- Draft Plan：在既有离线 Final Plan 范围内，只跑 verifier 聚焦 mock，失败才做测试先行
  的最小修复；无失败则仅补证据。
- Architecture Review：`Architecture Impact: None`；`Findings`：本次仅离线测试回执，
  不启用 CI→Artifact 外部分发，不变更 ACWM/产品运行权威、数据/恢复、权限/Workspace、
  Migration/Legacy、Provider、Release/Apply、Deterministic/Live 边界；
  `Required Revisions`：区分合成 REST 与真实服务、混合 Python 环境及磁盘时点；
  `ADR Required: No`；`Architecture Document Delta: None`；`Outcome: Approved`
  仅对本次局部事实记录。
- Revise Plan → Final Plan：保持代码/测试/工作流原状，在本文追加精确命令、结果和
  未验证边界。
- Implementation → Architecture Reconciliation：本次只修改本文档；
  `ARCH-20260927-02` 继续 `Accepted/Not Implemented`，B0 Artifact 留存与同 run
  真实回读仍未实施；不推论 S1、Gate 或 Apply。

## 2026-09-28 授权后的候选分支专用 Final Plan

本节是新授权下的计划增量，不追改上述 2026-09-27 权限拒绝事实。用户明确授权在公开仓库
`Jinchengawu/DEV-Agent-Teams-V2` 的当前候选分支修改、提交、推送 CI 工作流并触发**一次** CI；
唯一经 B0 审计的同 Revision allow-list Bundle 可含产品 wheel 和 Console 静态资源，以 GitHub
Actions Artifact 留存三天并在同 run 只读回读。不授权 main 合并、Gate、Apply、购买额度或产生额外付费。
当前候选 ref 是 `refs/heads/codex/method-pack-s1-candidate-20260926`，推送前必须重新核远端
`before=f26ae975965773a5572a753b801c2e90e2e1f820`；新 commit SHA 才是本次 CI/Bundle 身份。

### Draft Plan → Architecture Review #3

Draft：沿用现有 Pytest、Python wheel、Console build、B0 静态审计成功顺序，然后仅对该候选
ref 的可信 `push` 运行上传前复审、单一 Artifact 上传和同 run 原始 ZIP 回读。PR、fork、main、
其他分支及普通后续 push 不上传；B0 或任一新增步骤失败均使 `quality` 失败。

| 字段 | 结论 |
| --- | --- |
| Architecture Impact | `Cross-boundary`：CI Bundle 进入 GitHub Artifact 外部分发边界。 |
| Findings | 旧设计的 main-only 范围与本次候选分支授权冲突；仅凭 ref 条件会让未来 push 自动上传。当前 verifier 的合成 ZIP/HTTP 测试不是 GitHub 原生回读；账户共享存储用量和费用仍未知。ACWM、产品运行态、业务数据、Release/Apply 与 Migration 不变。 |
| Required Revisions | 三个新步骤统一锁定精确仓库、事件、ref、远端 `before`、一次性 commit message 与 `run_attempt=1`；仅唯一 B0 receipt 对应的 allow-list Bundle；固定 upload action SHA、三天、缺文件报错、无隐藏文件；同 run action ID/digest→REST metadata→原始 ZIP→Manifest/内部 Hash 失败关闭。提交前只 stage 受审 B0 文件；推送前独立核零额外付费余量，不可证明时停在本地。 |
| ADR Required | `Yes`，修订 ADR-0020 的外部分发与可信 ref 边界，不新增 ADR。 |
| Architecture Document Delta | 修订 `ARCH-20260927-02` 的 branch-only 范围；本地接线只能保留 `Accepted/Not Implemented`，真实回读通过后再按证据对账。 |
| Outcome | `Revise` 原 main-only Final Plan。 |

### Revise Plan → Architecture Review #4 → Final Plan

Revision：三个步骤共享 GitHub `if` 前置条件：`push`、仓库及事件仓库均为
`Jinchengawu/DEV-Agent-Teams-V2`、ref 为上述候选分支、`github.event.before` 为
`f26ae975965773a5572a753b801c2e90e2e1f820`、`github.run_attempt == 1`。
`prepare` 随后对这些字段和本次单行提交消息
`ci(b0): one-shot artifact 2026-09-28 8d4e71a2` 作大小写敏感严格复核；异常须使 job
失败，而不是静默跳过上传链。它防止普通后续 push 或重跑
意外再次上传；有写权限者仍可强制回退 ref 或故意重用消息，因此不是不可重放的安全令牌。
本次禁止 force push、第二次 push/重跑；未来候选分支若继续开发，须另行审阅并移除或变更该接线。

上传只使用 `prepare` 对 B0 receipt 与唯一 Bundle 做重新审计后输出的目录，不上传临时父目录、
receipt、JUnit、日志、缓存或业务数据。采用固定 SHA 的 GitHub upload action，
`if-no-files-found=error`、`include-hidden-files=false`、`overwrite=false`、
`retention-days=3`。job 仅有 `contents:read` 与 `actions:read`；GitHub token 仅传给只读回读步骤，
不传给 302 签名 URL；失败不降级 warning。测试先锁定三个步骤的相同条件、顺序、权限、
上传范围和 PR/main/其他 ref/后续 push/rerun 负例，再做最小工作流接线。
GitHub 表达式的字符串 `==` 比较不区分大小写，因此三个 `if` 只是前置筛选；`prepare` 还须
对 `GITHUB_EVENT_PATH` 的事件/仓库/ref/before/提交消息与相应 runner 环境作大小写敏感的
严格复核。大小写变体或不一致使 job 失败，Upload/Readback 不执行；错误输出只含固定代码，
不回显事件正文或 token。

Architecture Review #4：`Architecture Impact: Cross-boundary`；`Findings`：修订后不扩大 main 或
其他 ref 的自动分发范围，Build/Distribution 仍不拥有 Release/Apply 权威；`Required Revisions`：
推送前验证远端 `before`、精确 staged 文件与费用条件，真实回读失败保持失败关闭；
`ADR Required: Yes`（ADR-0020 修订）；`Architecture Document Delta`：更新
`ARCH-20260927-02` 的候选 ref 决策与证据状态；`Outcome: Approved`，仅批准本地实施与受控
推送前检查，不豁免费用止损门槛。

Final Plan：实现并本地验证上述 B0 工作流/验证器/聚焦测试；仅 stage 与该切片相关且完成复核的
文件，确保其他未提交 S1/FD 修改不进入新 commit。推送前只读核仓库仍为 public、runner 为标准
`ubuntu-latest`、远端 ref 仍是冻结 `before`、账户共享 Artifact/Packages 存储当前与本计费周期
累计用量/额度及预算，并按本次最多 24 MiB ZIP、三天留存评估。若不能证明不会产生额外付费，
允许停在本地 commit，但**不得 push**，不得购买额度、改账单设置、以第二次 CI 试错。
真实 run 的 Artifact ID、digest、REST metadata、ZIP SHA 与 Bundle Manifest/内部 Hash 必须
逐项回读后才能将本条记为远端 B0 成功；仍不能换算为 S1 独立安装或正式 Release Gate。
