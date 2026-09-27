# 四仓 R2 交接证据索引

## B0 CI 静态 Bundle 审计与 Artifact 回读候选（f26 历史状态）

B0 已在 CI `quality` job 接入同次 JUnit、既有 Builder 和静态审计；Bundle 与脱敏回执只留在
runner 临时目录，不新增 Artifact 上传。主任务委派 Agent 对 GitHub API 元数据的只读回执为：
[`f26ae975…` push run 36302936929](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/actions/runs/36302936929)
`completed/success`，其 [quality job 108573912485](https://github.com/Jinchengawu/DEV-Agent-Teams-V2/actions/runs/36302936929/job/108573912485)
第 21 步 `Audit CI static delivery bundle` 亦 `completed/success`，该步骤无
`continue-on-error`；PM 将其分类为 B0 静态审计远端成功。本子任务未独立查询远端日志或原始
runner 临时回执。该 run 的 Artifact API `total_count=0`，因此没有同 SHA Artifact 留存。
仓库 API `visibility=public`、`private=false` 是已核仓库可见性事实，不证明未来 Artifact
或日志可被匿名读取。B0 静态成功也不是 S1 独立安装、可复现构建、Browser/Deterministic/
Live Gate 或 Release/Apply。不得把本机 wheel、direct Vite 诊断 dist 或本节计划当作已
留存的同 SHA 制品。

当时本地增加了上传前范围复核和同 run REST 原始 ZIP 的离线验证器合同候选，但该 f26
CI 工作流尚未接线。拟议的未来 push 上传修改被自动权限审查拒绝；不得改走其他入口绕过。
Artifact 具体可见性、Actions/存储额度与费用仍为 `Unknown`；三天保留期、
上传 action 权限和原始 ZIP digest 语义须在获准后的真实 CI 中核验。
这些未知不影响离线脚本测试，但阻止将其描述为已经留存的交付制品。

现有 B0 审计先消费 Bundle Verifier 的 Manifest↔实际路径/size/SHA、Product Root 与链接
完整性结果，再施加有限的路径类别、容量、ZIP 成员与具体凭据值/格式扫描。它不重复或证明
Builder 的完整 allow-list，也不能证明“绝无秘密”。Manifest 顶层最多 200 项、wheel ZIP
成员最多 400 项，非 wheel 文件加 wheel 展开成员合计最多 16 MiB，单件最多 8 MiB；
拒绝路径逃逸、链接、嵌套归档及未知二进制 wheel 成员。普通 `token` 或
`Authorization` 字段名不是凭据值。CI 用同次测试的测试 ID 和跳过原因确认唯一预期
Live Codex 跳过项；本子任务没有复核该 run 的 JUnit 原始字节。

未来上传归档的 Artifact digest 与 Bundle 内 `delivery-manifest.json` SHA-256 是不同身份，
独立下载的 digest warning 或任何文件复核不一致均须硬拒。`uv build` 维持既有隔离方法，
Hatchling 与传递构建依赖来源暂记 `Unknown`，不得宣称完整可复现。审计失败、CI 失败或
资格不明时不得上传；该 f26 轮 Artifact 候选没有真实上传、回读、提交或推送证据。

### 2026-09-28 本地接线与远端止损门槛

用户后来明确授权在公开仓库当前候选分支做一次受控 B0 上传与同 run 回读，不授权 main、
Gate、Apply 或额外付费。隔离候选本地已加入 `prepare → upload → readback` 工作流切片：
只在候选 ref、冻结 f26 `before` SHA、首次 run 的可信 push 前置筛选后执行；`prepare` 对
事件文件和 runner 环境做大小写敏感严格复核，包括本次唯一提交消息、非新建/非强推和
推送后 SHA。上传仅使用重新审计的唯一 allow-list Bundle，留存三天，回读 action ID/digest、
REST metadata、原始 ZIP 和 Bundle Manifest/内部 Hash；异常失败关闭。

本段是**本地实现与测试证据**，不是已提交/推送或远端 Artifact 证据。聚焦 B0 测试
`71 passed`、Ruff、Python 3.11 语法、工作流 YAML 和三段 shell 语法检查通过；
真实 GitHub Action、Artifact 可见性、ZIP digest 同义、账户共享存储和费用仍未验。
推送前必须重新确认远端 ref 仍为 f26，并由账户有权限者提供当前及本周期累计
Artifact/Packages 共享存储用量、套餐额度与预算回执，证明最多 24 MiB ZIP 三天留存
不会产生额外费用。当前 CLI 无读取该摘要的授权；未经费用证明只能停在本地，
不得以本仓 Artifact=0 或公开仓库 runner 免费代替账户级账单判断。

## v0.5.1 Local Evaluation Delivery Candidate

交接前先在已构建 Console 的同一 Revision 上构建 Python 制品与 Delivery Bundle：

```sh
uv build
pnpm --dir console build
.venv/bin/python scripts/build_delivery_bundle.py \
  --output-root <空的输出父目录> \
  --wheel dist/dev_agent_teams_v2-0.5.1-py3-none-any.whl
.venv/bin/python scripts/verify_delivery_bundle.py \
  <输出父目录>/agent-team-os-0.5.1
```

Builder 仅收集 allow-list 内的 wheel、`console/dist`、`migrations/*.sql`、四个锁定
config、`pyproject.toml`、`uv.lock` 和默认 `agent-team-os-mvp/1.3.0` Evaluation Dataset，并生成
`delivery-manifest.json`。缺失资源、版本不一致、符号链接、数据库、日志、
`.env`、密钥或凭据文件均 fail-closed。Manifest 的 `evidence_scope` 固定将 Deterministic/
Live 标为 `not_run`，将 Approval/Apply 标为 `not_authorized`。
正式 Bundle 构建会拒绝 dirty worktree；`--allow-dirty` 仅用于开发验证，Manifest 必然标记
`source_worktree_clean=false` 与 `local_bundle=development_only`，不得作为交付候选。

当前 Builder 生成 `agent-team-os-delivery-bundle-v2`，先核对 wheel 后端文件与源码完全一致。
Runtime 不依赖 Bundle 外的 `.git`：启动前校验整个 Bundle、实际加载后端与 wheel、依赖锁和
干净构建身份。历史 v1 包仍可校验归档完整性，但不支持安装运行身份，须重新构建 v2；不得复制
开发 checkout 的 `.git` 到包内绕过。校验是可信本机分发的完整性检查，评测者仍需核对交付的
Manifest Hash，不将自报 SHA 视为数字签名。

解压或复制 Bundle 后，启动命令必须显式设置：

```sh
AGENT_TEAM_OS_PRODUCT_ROOT=<Bundle根目录> \
AGENT_TEAM_OS_DATA_DIR=<独立临时数据目录> \
AGENT_TEAM_OS_VERIFICATION_TOOLS_DIR=<已准备的锁定验证工具环境绝对路径> \
agent-team-os demo
```

Frontend 验证工具环境须包含产品生成的 `environment.json` 与 `node_modules`；不能直接指向任意
开发依赖目录。此环境不随 Bundle 分发，需要预先按工具环境合同准备。未指定上述变量时默认目录为
进程 cwd 下的 `.agent-team-os/verification-tools`，不会跟随 Product Root；非 checkout 启动应使用
绝对路径，否则资格化可能返回 `WORKCELL_VERIFICATION_ENVIRONMENT_UNQUALIFIED`。

Product Root 会严格校验 config、Migration、`console/dist/index.html` 和默认 Evaluation Dataset；显式 Root 非法时
不回退到 checkout。评测账号必须为本次交接独立生成，密码不得进入 Git、日志、
Fixture、Report、截图或文档。Bundle 校验成功仅支持 `locally_verified` 判定，不得换算为
下文的四仓正式交接。

Bundle 不携带从 Registry 下载的 Method Pack 对象。首次启动前，在有网络且明确授权
下运行已安装 wheel 提供的入口；它会使用 Bundle 中锁定的 URL、Registry Integrity 和
SHA-256 验证内容，不会把归档写回 Bundle：

```sh
AGENT_TEAM_OS_PRODUCT_ROOT=<Bundle根目录> \
AGENT_TEAM_OS_DATA_DIR=<独立临时数据目录> \
agent-team-os-method-packs
```

此入口只接受 Bundle 锁中的两条精确 `registry.npmjs.org` HTTPS 归档；禁代理、禁跳转、
验证 SHA-256/SRI 与 content/qualification 后才在同一 Store 排他锁内发布两包。
正式读侧取共享锁，看到 `.install-in-progress` 必须停止；崩溃残留不得自行删除、
复用旧缓存或改用镜像，应保留现场并另行审批恢复。
新对象目录的原子提升必须拒绝先占目标：macOS 使用 `renamex_np(RENAME_EXCL)`，
Linux 使用 `renameat2(RENAME_NOREPLACE)`，且仅在当前本地文件系统实际支持时可用。
缺 libc 符号、内核/文件系统不支持、跨挂载或目标先占均须失败关闭；不得回退到
普通 `rename`、`replace`、先查再改或目录复制。目标/本批 staging 归属不确定时
保留 pending，禁止自动清理、重试另一来源或给出 `ready`。Linux 原生支持须由
同 SHA 的 Linux CI 正反测试确认；macOS 本地测试或 fake libc 参数断言不替代。
既有私有 Store 的构造与读取不会自动补 `objects/sha256` 或 `.install.lock`；缺锁、
缺对象结构或 pending 均失败关闭且无读前写入。只有 Writer 在无 pending 的预检后
建立常驻 owner `0600` 锁，持排他锁并先标 pending，才允许发布新结构。
缺根 Store 的构造、Preview/Gate App 构造与正式 Reader 也不创建目录；Reader 缺根
直接失败关闭。仅安装 Writer 可在已存在的安全父目录下独占创建 owner `0700` 根，
缺父目录时停止，不递归补 Data Root。
首次建根时父目录 inode 漂移会失败关闭；只可清理经同一父目录 fd 核实仍属本批的
空根。若身份或清理不确定，保留残余现场并申请独立恢复，禁止继续建锁、pending、
对象或重试另一来源。child-open 后必须核 owner、精确 `0700` mode 与空目录；
不得用 `fchmod` 修复归属未证实的新根。受 umask 收紧、替换或非空目录时保留现场并
报 `METHOD_PACK_STORE_RECOVERY_REQUIRED`。本地方案依赖合作同 UID Writer；恶意
同 UID 瞬时换入空 `0700` 目录的绝对防护未获证明，需另审平台级原子方案。
Release Browser Fixture 的新目标由创建者独占建 owner `0700` 根与自己的 owner
`0600` 锁；不复制源锁，释放源共享锁后才取目标共享锁做资格复核。复制或复核失败
只清本次新目标，既有源 Store 不变。
Preview readiness 仅把安全地确认不存在或 owner `0700` 且全空的私有 Store、以及已有锁但
缺冻结 Snapshot 的情况列为 `missing` 并给安装指引；非空缺锁、pending、链接、权限或
身份不确定状态列为 `failed`，不提示自动重装，也不为检查而创建 Store。
Data Root 应为本次新建的当前用户私有 `0700` 目录；既有非私有目录不静默修改权限。
CLI `status=ready`
仅表示当前锁定 Method Set 的本地资格；源码脚本/自定义锁只能得到
`source-qualified`，均不能替代独立安装、Browser、Deterministic 或 Live Gate。
源码默认运行可能先有 owner `0755` Data Root；仅当父链同 UID、不可他人写且 Store
自身以 owner `0700` 独占新建时，才视为私有 anchor。显式 S1 安装仍必须使用新的
owner `0700` Data Root；既有 `0755` Store、链接、可写/异 UID 父链或 inode 漂移
直接停止，不通过 `chmod` 或旧缓存修复。
回执仅含 Product/Manifest/Lock、官方 URL、禁代理/跳转与归档/内容/资格 Hash，
不得包含凭据、归档正文或秘密请求头。
Method 安装在配置解析、下载、发布、FrozenSet 和回执间使用同一已验 Lock 字节；
`ready` 前再次完整验证 Bundle 并比较 Manifest/Lock 身份。检查点可观察的中途换锁、
同字节替换 inode 或任一 Bundle 文件在权威重验时失配均无成功回执；已完整发布但
未获 `ready` 的对象不等于 S1 验收。
本次安装须从同次权威校验起持有 Bundle Manifest/Lock 或 source Lock 的 no-follow
fd 及受核父目录身份至结束，异常必须关闭；各阶段核 fd/路径/冻结字节，防止普通
双替换造成 inode 复用 ABA。进入此路径前保证 owner-private 受控根、合作 Writer
停写；source 自定义锁仍不是 Bundle-ready。fd pin 与检查点不证明在仍可写的目录中
路径每一瞬连续；恶意同 UID 写者若在威胁范围内，停止并先取得另审的 OS 强制
写隔离，不可拿本地焦点测试、CI 或最终 Hash 冒充该保证。

## Formal Live 交接

`scripts/check_delivery_handoff.py` 只关联已有原生报告。`reference_check=consistent` 表示三轨引用
与目标相容，不能代替 Release Report、业务 Gate 或当前远端状态。工具不会运行 Agent、访问业务服务、
批准 Gate 或修改产品数据库。

正式交接的目标为 `four-repo-r2-alpha`。三个输入必须绑定同一干净 Product Revision 和 ACWM Revision，
各自原生 `fail=0`、`warn=0`、`skipped=0`，Browser 与 Live 的 Build Identity 必须一致：

| 输入 | 原生内容及范围 | 原生 Hash |
| --- | --- | --- |
| `--core-browser` | `core-browser-run-receipt-v1`，R2 scenario，七个 Context 和五个 Workcell 的完整实际浏览器断言 | `receipt_sha256` |
| `--deterministic-gate` | 既有 `GateReport(kind=deterministic)`，保留其单后端/多 Pipeline 基线范围 | `evidence_sha256` |
| `--live-release` | `release-acceptance-report-v2` / `feishu-knowledge-delivery-v1`，现有四仓 R2 完整检查集 | `report_sha256` |

浏览器 Runtime 是 `deterministic-model-boundary`，不能写为 Live。Delivery 上的 Planning/Evidence
身份和可能为空的 Execution 身份原样保留；每个 Workcell 的实际 Runtime 另行断言。
Live Report 本身没有 Runtime identity 字段；索引仅以 `planning_adapter_verified` 和
`execution_adapter_verified` 引用已通过的 Codex/Hermes Binding 检查含义，不补造 Runtime identity。
各轨 Project、Delivery、Pipeline 和 Candidate 可以不同，索引保留实际值，不合并成一次运行。

原生浏览器驱动的严格 `--receipt` 模式会在启动时使本次指定收据路径失效。
完整 UI、产品证据、Console 无错误、当前 HTTP Bundle 与本机 dist 一致、运行前后干净 Build 一致
都通过后才原子生成成功收据；失败不会保留该路径上次的成功输出，其他历史报告仍保留。
R2 入口复用 `scripts/browser_feishu_knowledge_e2e.py --gate-c`；基础四仓入口生成的
`knowledge_scope=null` 收据仅可归档，不能满足 R2 交接。
缺少 Knowledge 配置、部分 Stage 成功、Readiness ready、Checkpoint、截图和 CLI exit 0 都不能补足收据。

先提交并冻结工具、产品及文档 Revision，再执行三轨验收。生成的索引写入忽略的报告目录，避免改变
已验收产品 SHA。下面路径需要替换为本次实际原生报告；不自动选择“最新”文件：

```sh
.venv/bin/python scripts/check_delivery_handoff.py \
  --product-revision <完整ProductSHA> \
  --acwm-revision <完整ACWMSHA> \
  --core-browser .agent-team-os/reports/<R2浏览器收据>.json \
  --deterministic-gate .agent-team-os/reports/<确定性报告>.json \
  --live-release .agent-team-os/reports/<V2Live报告>.json \
  --output .agent-team-os/reports/<版本>/handoff-index.json
```

退出码 `0` 仅表示引用检查 `consistent`；`2` 表示 `incomplete`、`invalid` 或调用错误。
缺轨/基础浏览器缺 R2 为 `incomplete`，错 Revision/Build、内容 Hash 不符、未知 Schema/检查码或
失败报告为 `invalid`。索引保留明确问题及原生 Hash，另外记录文件字节 Hash用于核对所引用的文件。
这些 Hash 表达内容完整性，不替代原生 runner 的实际执行断言。旧报告不会自动迁移或补填资格。

交接还需按 P0-05/P0-06 保留四仓 Candidate、PR、Verification、Review、Bundle、Apply Receipt、
Manifest、health 和隔离故障验证的原生证据。索引只引用已有 Report 中的证据 Hash，不能创建缺失证据。
独立评测账号仅记录已安全交付这一事实；密码不得进入索引、报告、截图或 Git。
