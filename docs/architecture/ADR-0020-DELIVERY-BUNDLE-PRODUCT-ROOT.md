# ADR-0020：v0.5.1 Delivery Bundle 与 Product Root

状态：已实现并完成本地合同验证
日期：2026-09-20
架构变更：`ARCH-20260920-01`

## 背景

Preview Runtime 曾从 `Path(__file__).parents[2]` 推导 config、Migration 和 Console 位置。
该假设只对源码 checkout 成立，Python wheel、Console dist 和运行资源也没有一个绑定
同一产品 Revision 的交付单元。

## 决策

1. 交付单元是版本化 `Delivery Bundle`，而不是宣称 wheel 单独自包含。
2. Bundle 以 allow-list 组合后端 wheel、`console/dist`、checksummed Migration、锁定 config 和
   Preview `EvaluationService` 启动必需的默认公开 Evaluation Dataset；
   Manifest 记录产品版本、Git SHA、相对路径、大小和 SHA-256。
3. `AGENT_TEAM_OS_PRODUCT_ROOT` 是显式运行配置。一旦设置，Root 必须完整且校验通过；
   非法时 fail-closed，不得静默回退到源码仓。未设置时保留 checkout 开发兼容。
4. Bundle 拒绝符号链接、数据库、日志、缓存、`.env`、密钥和凭据型文件；运行数据
   继续由独立 `AGENT_TEAM_OS_DATA_DIR` 持有。
5. Manifest 是制品完整性事实，不拥有 Release Gate、Approval 或 Apply 判定权。
6. 正式 Bundle 拒绝 dirty worktree；开发例外必须在 Manifest 标记 `development_only`，
   不得冒充与 Git SHA 一致的冻结候选。

## 权威与兼容

- 不改变 ACWM、Workcell、Verification、Release Acceptance 或 Forward-only Apply 权威。
- 不改变数据库 Schema 或历史 Snapshot。
- 源码 checkout 无显式 Product Root 时保持现有开发路径。
- 旧 `0.5.0` 制品继续作为历史文件，不被追认为 `0.5.1` Bundle。

## 被拒绝的方案

- 将全部仓库复制到交付包：会携带运行态、开发依赖和非必要文件。
- 显式 Root 无效时回退 checkout：会产生伪 clean-room 通过。
- 新建交付 Gate：会复制现有 Handoff Evidence 和 Release Acceptance 权威。

## 验证边界

版本一致性、Bundle 正反合同、Manifest 篡改拒绝、真实 Evaluation Dataset 加载、Product Root 显式选择和无回退由
自动化测试覆盖。本地 Bundle 成功只是 Local evidence；Deterministic、Live、Release Approval、
Apply 与 read-back 仍需各自的同 Revision 证据和授权。

## 2026-09-22 修订：安装制品的 Build Identity

clean-room 安装证实 Bundle 无 `.git` 时原有 Build Identity 无法创建，阻断 NR-01 的用户主路径。
本修订为 Cross-boundary，沿用 DeliveryBuildIdentitySnapshot 字段和 Hash，不迁移历史快照。

- Builder 校验 wheel 中完整 `agent_team_os` 文件集合与当前源码一致，拒绝旧 wheel 混装；Bundle 增加 `pyproject.toml`、`uv.lock` 保留 ACWM 声明与解析资格检查。
- 发现 Delivery Manifest 时，Runtime 首先校验全部 Manifest 文件 Hash、路径和符号链接，再校验实际加载 package 的全部非缓存文件与唯一 wheel 的字节一致。
- 仅上述检查通过且 Manifest 为合法 SHA、`source_worktree_clean=true` 时，从受信分发 Manifest 冻结 Product Revision；无 Manifest 的源码 checkout 保留 Git 身份路径。
- Manifest 完整性与 wheel 一致性是可信本机分发合同，不是数字签名或对抗篡改者重新制作整包的来源认证；交接者仍需核对独立传递的制品 Hash。
- 既有 Approval/Apply、只读 V2 验收和 ACWM 权威不变。开发 dirty Bundle 不能作为正式运行身份。
- 新构建使用 `agent-team-os-delivery-bundle-v2`；历史 v1 仍可校验归档完整性，但不能启动安装制品 Runtime。组合根在数据库写入前拒绝旧包并给出重新构建提示，不在创建 Delivery 时产生迟到的错误。

测试覆盖无 Git Bundle、源码身份回归、混装/额外代码、资源漂移、dirty/非法身份与内部符号链接。独立安装和最终 Revision 三轨的执行结果见下一阶段实施记录，不由此 ADR 宣称完成。

## 2026-09-26 修订：Method Lock 的已验字节身份

Bundle 完整性校验须在同一次权威 verifier 调用中，以 no-follow 文件描述符读取并校验
Manifest 原始字节及其 `config/method-packs-v050.json` 条目。返回的 Manifest Hash
来自实际解析的同一组字节；供 Method 安装消费的 Lock Bytes、大小、SHA-256 和 inode
必须与该 Manifest 条目一致，不能在校验后重新打开路径并把新内容冒充已验内容。
安装输出 `ready` 前再次执行完整权威 Bundle 校验，并比较冻结的 Manifest/Lock
Hash 与 inode；若中途路径替换、内容漂移或其余 Bundle 资源失配，则失败关闭、无
`ready` 回执。此约束是单次安装的可信快照合同，不使可写文件系统在命令返回后
永久不可变，也不把 Manifest 升格为来源签名或 Release/Apply 权威。

### 同次安装的 fd 生命周期与 ABA 边界

Method 安装在权威 verifier 读取前，以 no-follow fd 固定 Manifest 与 Lock 原对象；
verifier 在同一次校验中消费这两个 fd 的原始字节，并核 Manifest 条目与 Lock
大小/SHA-256。fd 连同受核父目录身份持有到安装成功或异常退出，配置、下载、
发布与回执只消费冻结字节；各检查点比较 fd、路径、字节和完整 Bundle 资格。
普通两次 `os.replace` 即使内容相同，也不得借释放并复用最初 inode 形成 ABA
而发出 `ready`。异常退出必须关闭 fd；提交后才发现漂移时不捏造成功回执，
也不擅自回滚已完整发布的旧/新 Store 对象。

本合同采用受控 owner-private Bundle 根、合作 Writer 停写的本地威胁模型；
同 UID 恶意或不合作进程在可写目录内换走又恢复**原 inode**，不能由 fd pin
加离散检查点证明“路径每一瞬均未替换”。因此上述“中途路径替换失败关闭”指
本模型内可观察的漂移及已排除的 inode 复用 ABA，不是对任意可写目录的
绝对时间连续性证明。若要纳入对抗性同 UID 写者，须另审由 OS 强制的目录
写权限隔离；仅加最终 Hash、mtime 或合作 `flock` 均不构成该保证。

## 2026-09-27 修订：B0 CI Artifact 留存与同 run 回读（待接线）

CI `quality` 的 B0 静态审计与未来 GitHub Actions Artifact 留存属于 Build/Distribution 的
外部分发信任边界，不构成新的 Release、Approval、Apply 或 ACWM 权威。上传范围只能是
B0 成功后重新审计的唯一 allow-list Bundle 目录；不从 runner 临时父目录、receipt、日志、
数据库、秘密或未审计路径取材。仅可信仓库 `push` 可进入上传与同 run 回读，PR 不上传；
上传失败、缺文件、回读失败或身份不一致必须使 `quality` 失败，不得降为 warning。

回读应以 action 的 `artifact-id` 与 `artifact-digest` 定位当前 run 的 REST metadata 和原始 ZIP；
独立核对 metadata 的 ID、name、run ID、head SHA、digest 与 ZIP 字节 SHA-256，随后限量
安全解包并重跑 B0 Bundle/Manifest/内部文件校验。Artifact ZIP SHA 与 Bundle Manifest SHA
是不同对象，不能相互替代。REST 302 的签名 URL 不得接收 `GITHUB_TOKEN`，token、URL 与
异常响应正文不得进入日志。最小 token 权限为 `actions:read` 与 `contents:read`。

本地离线验证器和模拟测试不代表上述 CI 接线或远端回读已发生。未来 push 自动上传的工作流
编辑被权限审查拒绝，当前维持 `Accepted/Not Implemented`；仓库公开性已由 GitHub API
确认，但未来 Artifact 具体可见性、存储费用及真实
GitHub ZIP digest 语义仍需单独确证。不得凭静态 Manifest 自报 Hash 或本地合成 ZIP
升格为正式交付、S1 或三轨 Release Gate 证据。

## 2026-09-28 修订：本次候选分支的单次 B0 分发范围

前节记录的是 2026-09-27 的权限和设计状态。用户现已明确授权在公开仓库
`Jinchengawu/DEV-Agent-Teams-V2` 的当前候选分支做一次 CI 上传与同 run 回读；这不追认
旧 main-only 范围，也不开放 main、PR、fork 或其他分支上传。可信触发须同时精确绑定仓库、
`push`、`refs/heads/codex/method-pack-s1-candidate-20260926`、冻结的远端
`before=f26ae975965773a5572a753b801c2e90e2e1f820`、首次运行和本次唯一完整提交消息。
该组合阻止普通后续 push 与 rerun 意外再分发，但不抵御有写权限者故意 force-push 回旧
`before` 并重用消息；本次禁止 force-push 和第二次 push，后续修改分支前须另行移除或变更
接线。上传内容、B0 审计、REST/ZIP/Manifest 双身份及失败关闭规则保持前节不变。
GitHub 表达式字符串相等比较不区分大小写，故仅靠 workflow step 的 `if` 不足以保证精确
ref/仓库/提交消息；上传前 `prepare` 必须大小写敏感地复核事件文件及 runner 环境，失配以
固定错误码失败关闭，不向日志回显事件正文或秘密。

这项决策只改变 CI Build/Distribution 的外部出口，不改变 Product Root、Manifest、ACWM、
产品运行态、Release/Approval/Apply 或数据库权威。工作流本地接线和 mock 测试不证明真实
GitHub 存储、下载权限、ZIP digest 或零额外费用；真实同 run 回读通过前，架构条目继续
`Accepted/Not Implemented`。公开仓库的标准托管 runner 分钟数免费，但 Artifact 存储与
Packages 共用账户额度；推送前若无法只读核清当前及本周期累计共享存储、额度和预算，必须
停止在本地，不以小制品或三天保留推断零额外付费。不购买额度或修改账单设置。
