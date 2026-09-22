# 下一阶段实施记录

日期：2026-09-22。执行 Owner：software_delivery_engineer；验收由主任务委派 product_manager 独立执行。

## 范围与基线

按 NEXT-STAGE-DELIVERY-READINESS.md 的 NR-01–08、NEXT-STAGE-DELIVERY-PLAN.md 的 S0–S6 推进。
当前状态为 implementing，不是 Release accepted。源码基线为 `5395a4bc1a2ed5960c774a7f12bb417f896053a5`，Package 版本为 `0.5.1`；v0.5.2 是历史交互工作名称，不能代替制品版本。
预存两份产品计划为 untracked，保留。现有 8091 服务和数据库不作评测写入。
隔离目录：`/private/tmp/atos-next-stage.5JqIBk`；其中 source 为该 HEAD 的独立 clean clone。
该 clone 不含此次计划与实施记录；其证据只覆盖上述代码 Revision。

## Draft → Architecture Review → Final

Draft：首先复现已有 Builder、clean-room、Readiness、Browser、Deterministic 及恢复能力；复现缺陷后另作具体修复审查。

- Architecture Impact：None，本条仅为隔离验证与事实记录，不改变生产行为。
- Findings：保持 ACWM 跨 Stage 权威、产品 Workcell/Approval/Apply 权威；仅用已有公共入口与原生报告。数据隔离于临时目录；不迁移 Legacy 数据；保留故障、Lease、恢复证据和 Deterministic/Live 区别。
- Required Revisions：正式证据只能来自 clean clone 对应 SHA；任何修复后必须重新冻结并重跑。网络安装仅限锁定依赖和 Method Pack。
- ADR Required：No。
- Architecture Document Delta：无。
- Outcome：Approved，仅针对该验证/记录切片。

Final：执行 S0/S1/S2 的现状核对、S3 本地两轨、S5 隔离恢复；S4 在具体外写或业务决策前提供范围材料。不得将未执行的 S4/S6 写成通过。

## 已观察命令

1. 主工作树 focused tests：`.venv/bin/python -m pytest -q tests/test_delivery_bundle.py tests/test_external_forward_release_v2.py tests/test_project_release_recovery.py tests/test_readiness_api.py` → 24 passed，22.23s。
2. `git clone --local --no-hardlinks . /private/tmp/atos-next-stage.5JqIBk/source` → exit 0。
3. clone 中 `uv sync --frozen --all-extras` → 沙箱 cache 拒绝；获环境提权后 exit 0，CPython 3.12.11、119 packages，ACWM `ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed`。
4. clone 中 `pnpm --dir console install --frozen-lockfile --offline` → 缺离线 tarball；联网锁定安装 exit 0，275 packages。
5. clone 中 `uv build` → exit 0，wheel 与 sdist 版本 0.5.1。
6. `pnpm --dir console build` → 沙箱及提权均 exit 137；直接 `node node_modules/vite/bin/vite.js build`（cwd=console）exit 0，3539 modules，3.54s。没有据此修改依赖或源码。

## 验收状态

NR-01–08 均待本轮证据汇总；现有单元测试只支持对应合同，不代表三轨交接。独立账号密码不进入本记录。真实四仓业务 Gate、Apply 与事后 Live Report 尚未执行。

## 修复切片 A：无 checkout Bundle 运行身份

复现：安装 wheel 后 `resolve_product_root(Bundle)` 成功，但 `snapshot_delivery_build_identity(Bundle)` 抛 `Agent-Team-OS Git identity is unavailable`，使独立安装的 Delivery 无法冻结身份。

Draft：从验证过的 Bundle 读取构建身份，同时校验正在加载的后端与 wheel 一致，并保留依赖锁检查。

- Architecture Impact：Cross-boundary（Distribution 与 Runtime Build Identity 信任边界）。
- Findings：现有 Git-only 身份与 NR-01 无 checkout 合同冲突。Manifest 自述 SHA 不足以证明正在加载的代码；仅资源存在不足以判为可用。保留 Git checkout 模式及 Snapshot 原有字段和 Hash。
- Required Revisions：Builder 绑定 wheel 代码与当前源码，Bundle 纳入 pyproject.toml/uv.lock；Runtime 校验 Manifest 全资源、加载包与 wheel 全文件一致，拒绝混装/篡改。该校验是受信本地分发包完整性，不声明签名或供应链来源认证。
- ADR Required：Yes，修订 ADR-0020，并对账 ADR-0019 的 Git-only 描述。
- Architecture Document Delta：补充无 checkout 的 Build Identity 解析与其信任限制。
- Outcome：Approved。

Final：先加无 Git Bundle 身份测试及加载代码/资源篡改负例，再实现；修复后重建 clean-room 候选，旧基线结果不追认为新 Revision。

Revise：主任务审查要求验证实际 wheel 加载路径、内部 symlink、历史 v1 行为。已采用显式 Bundle v2；v1 仅保留归档校验，组合根在数据库写入前拒绝其运行身份。主任务审查上述修订无阻断。
Implementation：新增无 Git 身份、代码/资源漂移、dirty/非法身份、旧 schema、内部 symlink、旧 wheel 负例；源码 Snapshot 字段及原有 Hash 合同不变。首轮25项 focused tests通过，Ruff 与 Mypy（197 files）通过。新增负例及全量结果仍需最后回读。
Architecture Reconciliation：修订 ADR-0020、ADR-0019 与架构总览及 Runbook；此对账只证明实现机制，不替代最终候选独立安装与三轨验收。

## 外部依赖实测与权限边界

`live-v051` 的只读事实投影：4 个独立 external-git workspace 为 ready，4 项历史 direct-fast-forward 事实；当前子进程可解析 Git Credential 为0，四仓均 `WORKCELL_VERIFICATION_QUALIFICATION_CHANGED`；Published Revision 为 `agent-workcell-delivery-live:4`，7 Context、22 Provider Slot、Codex Planning/Workcell 已冻结。SQLite vec能力通过。
已批准 Feishu Source存在；凭据在当前进程未解析导致索引/模型资格下游计数为0，不能据此认定 Ollama 服务损坏。直接 `/api/tags` 返回已安装 `bge-m3:latest`、1024维；仍需与冻结资格比较。

后续复核：将现有 credentials.env 仅加载到子进程内存、并允许本机网络探针后，Feishu Credential、Source新鲜度、active index、passed retrieval evaluation、embedding qualification、live Ollama模型匹配计数均为1。前次0计数属于进程环境和沙箱限制，不是这些依赖当前故障。Git Credential解析仍为0，四个Verification Qualification仍因工具身份漂移拒绝；未刷新或写入原live数据库。

全量pytest首次沙箱结果为669 passed、1 skipped、3 failed、18 errors（socket PermissionError）。隔离测试提权重跑已获批，JUnit写 `/private/tmp/atos-next-stage.5JqIBk/full-suite.xml`；最终结果由该原生报告与后续验收记录判定。

Deterministic runner 首次在沙箱生成 `fail=1/warn=0/skipped=0`（PermissionError）。主任务附加 TemporaryDirectory 与 S3 授权证据复核后，自动审批仍拒绝执行，当前属于审批阻塞，未绕过。严格 R2 Browser 已实际发现切片B断言问题，失败路径未留下成功收据。

## 修复切片 B：R2 浏览器权威提示断言

复现：严格 R2 浏览器在组织模板页面遇到正确提示“执行顺序由 Published Pipeline Revision 管理。”，旧脚本用子串不存在断言而失败，未产生成功 Receipt。
Architecture Impact：Local；Findings：仅测试选择器与当前文案失配，不改变角色/流程；Required Revisions：改为正向验证完整权威提示；ADR Required：No；Architecture Document Delta：无；Outcome：Approved。以实际 Browser 复测验证，不删除产品证据断言。

## 修复切片 C：评测完成后的可见退出入口

Draft：R2 实测完成引导后评测仍 `awaiting_candidate_decision`，Lease 正确保留；运行室没有 Delivery 取消入口。Board 存在拖动拒绝路径但运行室没有指引，主流程无法直接继续正式交付。增加明确的评测结束动作。
Architecture Review：Architecture Impact `Local`；Findings：使用已有取消 API/CAS 与服务端权限，保持 onboarding、Delivery、Lease 权威及不自动 Apply；Required Revisions：只向获权用户在 ready 的评测候选显示结束入口，确认后取消，错误可见，取消终态后再给正式交付入口；ADR Required `No`；Architecture Document Delta 无；Outcome `Approved`。
Revise/Final：采用独立 EvaluationExit 视图与现有 Query Mutation 模式，不增加后台状态或接口。先测试角色/状态/显式确认边界，再走浏览器“完成引导→结束评测→新正式交付”，不以API代操作补齐用户流程。

Implementation/Reconciliation：新增组件与既有 cancel/CAS API 的 Query Mutation；取消错误保留重试上下文，cancelling 不开放正式交付入口，终态由后台确认。组件 9 项测试通过，Console 全量 33 文件、131 tests 通过，Typecheck 与直接 Vite build 通过。Browser 开发复测已实际完成评测→显式取消→新正式 Delivery→四仓确定性 Manifest；后续知识断言发现需先展开现有知识 Collapse，已补真实导航，尚待最终干净 Revision 收据。

## 后续核验（候选冻结前）

### 修复切片 E：独立安装的 External Git 能力探测

复现：`0cd21fc` Bundle 从 `/private/tmp` 启动，通过公开 API 创建四仓 Binding 后 verify 全部 409 `REMOTE_MAIN_APPLY_NOT_ALLOWED`。实际 `git push --dry-run` 在非 Git cwd 返回 `not a git repository`；同一现有 probe 测试从 `/private/tmp` 运行可稳定复现，不是 GitHub 登录失效。

Draft：probe 使用自身临时 bare repository 读取已确认的 main SHA，再执行原有非 force dry-run，不依赖产品 checkout。
Architecture Review：Architecture Impact `Local`；Findings：只修复 Git Adapter 的局部执行目录与对象准备，不改变权限、远端状态、Verification/Apply 权威或持久化模型；Required Revisions：每次隔离临时仓库、精确 SHA fetch、失败关闭、异常清理、no-checkout 与远端 refs 不变测试，dry-run 不升级为实际 Apply 成功保证；ADR Required `No`；Architecture Document Delta 无；Outcome `Approved`。
Revise/Final：先令已有公开 Adapter 测试切换非 Git cwd 得到 RED，再最小修复，覆盖 transport 拒绝、命令失败及临时文件清理；净化测试进程环境并采用短 traceback。此前被拒绝的正式 Gate/R2 不在本切片执行范围。

Implementation/Reconciliation：每次探测使用 scratch 下独立 TemporaryDirectory，初始化 bare repo并 fetch `ls-remote` 已验证的精确 SHA，再执行原有非 force `push --dry-run`；对象读取或远端并发变更失败均保持 fail-closed。仅在 probe 中去除继承的 Git 工作树/对象路径变量，Credential Reference 和其他命令策略不变。清理覆盖 init/fetch/push失败与成功；GitHub保护规则/服务端hook不能由无变更dry-run充分证明，既有 `direct_fast_forward_main` 字段不代表实际 Apply 已获准或一定成功。

验证命令使用 `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest -q --tb=short`：首个no-checkout测试RED（1 failed，0.55s）；`tests/test_external_git.py tests/test_project_workcell_bindings.py tests/test_external_forward_release_v2.py tests/test_project_release_recovery.py` 31 passed，29.35s。随后扩展公共 API 用例：非Gitcwd下managed/external四仓创建→验证→资格冻结→team激活，及verify 409后失败状态/无资格/清理/CAS重试，Adapter与该HTTP集合14 passed，6.56s（`probe-api.xml`）。Ruff、Mypy197源文件与diff-check通过。未新建提交、未重建Bundle、未刷新8096服务；该服务仍为`0cd21fc`，不能继承此未冻结修复。

S2安装步骤文档补充审查：Draft 是补齐已有 `AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR` 配置说明。Architecture Impact None；Findings 现有默认工具目录相对进程cwd，不会跟随Product Root自动解析，Runbook漏列非checkout启动时必须显式配置的前提；Required Revisions 明确绝对路径、既有锁定工具环境及失败码，不新增依赖或资格豁免；ADR Required No；Architecture Document Delta 无；Outcome Approved。Final/Implementation：在交付启动示例增加该变量并说明它指向包含environment.json与node_modules的产品验证工具环境，不是任意开发依赖目录。

S2 dirty真实只读复验：执行临时脚本 `reverify_live_safe.py`（cwd切至`/private/tmp`，环境白名单净化，现有GitHub凭据仅进程内注入）。四仓 `ls-remote → fetch → 非force push --dry-run → ls-remote` 全部ready，前后main SHA一致。首次隔离API三仓资格冻结成功，frontend因未指定绝对工具目录返回409；保留 `safe-capability-result.json` 与 `safe-isolated-api-result.json` 原结果。

第二轮显式配置已有锁定工具目录，四仓经公开create-binding/verify/qualify全部成功冻结资格，team-activate后项目active，`/v1/setup-readiness` 的8项global与7项project检查全部ready。证据为 `/private/tmp/atos-next-stage.5JqIBk/safe-capability-tools-result.json` 与 `safe-isolated-api-tools-result.json`；第二轮命令exit0，四仓API操作后的main再次回读一致，两处probe scratch均已清空，临时8097服务已停止。首次project初始化相关blocked是缺少frontend资格时未激活Team的下游状态，不是遗留初始化故障。

证据限制：这是`0cd21fc`基线上的dirty源码及新隔离项目（默认`agent-workcell-delivery`）准备度，不是旧`live-v051`数据库更新，也不是`agent-workcell-delivery-live:4`的Feishu/Ollama七Context完整Live Readiness报告。没有创建Delivery、Candidate、PR、批准Gate或执行Apply；原liveDB与远端引用不变，8096仍运行未含本修复的`0cd21fc`制品。后续须冻结修复、重建制品，补同Revision正式三轨及真实Live项目完整前置验证。

后续用户决策：明确授权本地提交此修复并重建隔离制品、更新原目标四仓资格及对应运行实例、运行隔离严格R2与Deterministic（仅夹具模拟审批/本地bare Apply）。这撤销对应隔离验收的旧禁止边界，不授权真实四仓Candidate/PR、业务Gate、GitHub main Apply或push/merge。运行结果写独立证据目录，仍须同Revision与零容差核验。

修复切片 D（NR-08）：实际 Browser 双向 Tab 测试发现桌面 Deployment Drawer 焦点跳到 BODY。依赖焦点锁只在 focusin 时修正，无法阻止 Tab 跳出浏览器内容；移动导航已有端点键盘修复。Draft/Architecture Review：Impact Local；Findings 保持 Ant Drawer 的 Escape、portal 与关闭回焦，只复用现有端点键盘行为；Required Revisions 抽取移动导航已有 FocusTrap 为共享组件并用于两个 Agent 编辑 Drawer，覆盖双向端点/禁用项/普通按键；ADR Required No；Architecture Document Delta 无；Outcome Approved。Final：先公共组件测试，再抽取与应用，最后完整 Console 与真实 Browser 复验。

切片 D Implementation/Reconciliation：共享 DrawerFocusTrap 应用于既有移动导航与两个 Agent 编辑 Drawer，保留关闭回焦，过滤 disabled/hidden/inert/负 tabIndex；空内容容器接管，React portal 不被强制抢焦点。测试先 RED（组件缺失），实现后2项专项通过；Typecheck通过，Console 34文件133 tests通过（19.53s），Vite3541 modules构建通过。实际 NR-08 Browser `nr08-debug11/result.json` passed，包含 Select 弹出层 Escape 后 Drawer仍开、双向24次Tab、Escape回焦、390px移动和限定角色动作；这仍是dirty开发回归，最终干净SHA证据另行生成。React检查清单未发现额外取数、全局事件监听或隐藏状态权威。

- 全量 Python：690 passed、1 skipped，229.88s，原生 `full-suite.xml`。运行跨首次冻结提交但生产源码内容未改变；该结果不是零 skipped 的正式 Gate。
- 相关 Browser receipt/harness 与恢复专项：59 passed，13.13s，`final-focused.xml`。三份修改后的 Browser 脚本 Ruff 通过。
- S2：将已授权 GitHub keyring token 仅注入子进程内存后 Git reference 4/4 可解析，无需用户重新登录。Feishu 权限新鲜度、索引与 Ollama 资格匹配均为 1；原四仓 Qualification 仍因身份漂移被拒。
- 在独立本地 clone 上重新 qualify 四仓全部通过：配置未变化；四仓依赖身份变化，QA/design/backend 工具身份变化，frontend 工具身份未变化。未更新原 live DB，没有把隔离资格成功宣称为原实例恢复。
- 安装制品公开 API 已在无 checkout 的独立环境通过登录 200、创建 Delivery 202、冻结 `4c82bdd` clean identity；该临时 Delivery 已回读 cancelled。独立账号尚未安全交付，不宣称 NR-01 全部完成。
- NR-08 扩展仅证明其实际覆盖范围：Viewer 发起入口隐藏与创建 API 403、Editor 发起入口可见、Admin 核心路径、Drawer 双向 Tab/Escape/关闭焦点回归与 390px 移动导航；不会外推三角色全生命周期权限覆盖。浏览器最终结果待冻结后记录。
