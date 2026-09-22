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
