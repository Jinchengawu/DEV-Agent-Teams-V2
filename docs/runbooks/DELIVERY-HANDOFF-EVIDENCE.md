# 四仓 R2 交接证据索引

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
`ready` 前再次完整验证 Bundle 并比较 Manifest/Lock 身份。中途换锁、同字节替换 inode
或任何 Bundle 文件漂移均无成功回执；已完整发布但未获 `ready` 的对象不等于 S1 验收。

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
