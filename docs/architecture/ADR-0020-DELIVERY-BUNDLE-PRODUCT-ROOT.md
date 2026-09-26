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
