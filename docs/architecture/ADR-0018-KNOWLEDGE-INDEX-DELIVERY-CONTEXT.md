# ADR-0018：不可变 Knowledge Index 与 Delivery Context

状态：已接受；Gate B 与 Gate C 本地原语已实现并完成 Deterministic/Browser 验证，复合变更
`ARCH-20260902-03` 仍为 `Accepted/Not Implemented`

日期：2026-09-02

架构变更：`ARCH-20260902-03`

Architecture Impact：`Critical`

## 实现对账（2026-09-02）

- Migration `0038`–`0043` 与 Knowledge Deep Module 已实现不可变派生索引、Embedding Qualification、
  Retrieval/Evaluation Policy、Shadow Build、CAS 激活、Context Preparation、Authorization Stamp、
  Attempt Admission、结果接纳和 Citation Receipt。
- `VectorIndexPort` 隔离了 Knowledge Application 与 `sqlite-vec` Adapter；Chunk 与 Embedding
  采用有界批处理，Published Index Profile 冻结 Chunk/Document 上限、切分参数与 80%
  容量告警阈值。
- Gate B 已完成 Deterministic API/浏览器闭环；Gate C 已完成 R2 Pipeline、七个 Stage Context、
  五个 Workcell/Citation 与 ReleaseManifestV2 的 Deterministic 浏览器闭环。
- 已增加独立 `feishu-knowledge-delivery-v1` Live Readiness 投影与 Receipt；它只读复用
  Project、Pipeline、Knowledge、Runtime 和 Framework Lock 权威，只返回 `ready/not_run` 或
  `blocked/not_run`，不创建新的 Gate/Release 权威。
- Readiness 不仅检查 CLI/凭据，还要求 Published Pipeline 的两个规划 Slot 绑定真实
  `hermes-provider`、五个 Workcell 的 20 个 Slot 绑定 `codex-cli-provider`，且产品
  Runtime Dispatcher 确实接线每个冻结 Adapter。产品现已接入 `hermes.acp` Role Turn，并以
  逐 Attempt 空沙箱、拒绝工具、实例/配置指纹、结构化 Schema 与 Citation 集合做失败关闭校验；
  `http.sync` 尚未接入。当前内置规划 Slot 仍为 `codex-simulated-hermes`，且尚无真实 Hermes
  Attempt 证据，故整体仍保持 `blocked/not_run`。
- ACWM Framework Lock 校验同时要求锁定 Revision 一致和 editable Worktree 干净，避免
  未提交 Contract 被当作可重放发布依赖。
- ACWM `0.5.1@ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed` 已发布
  `knowledge-context-v1` Stage Input Artifact Contract，并进入产品 Framework/Dependency Lock；
  干净 clone 可重放该依赖。不得在本仓库复制替代 Contract。
- 100,000 Chunk × 1024 维容量基准已在 Apple M1 Max / 32 GiB 开发机执行；它是
  [`deterministic_capacity_benchmark`](../evaluation/results/2026-09-02-knowledge-index-capacity-100k.md)，
  不是 bge-m3 生成性能或生产 SLA。
- 真实 Ollama 模型资格、Tenant App、Knowledge-bound Pipeline 端到端以及同 Revision Live Release
  Gate 尚未运行，故不能把本 ADR 或本地原语表述为完整自动 RAG。

## 背景

自动 RAG 会把可变外部知识、向量运行时、ACWM Stage 输入和 AgentAttempt 连接起来。如果在产品
SQLite 中原地维护大向量索引、在 Delivery 持久化前执行外部检索，或由 Agent 自行读取 Active
Index，就会破坏恢复、权限、Artifact Contract 和可审计性。

## 决策

### ACWM 与 Pipeline

ACWM Stage Artifact Contract 是 `knowledge-context-v1` 输入语义的唯一权威。Published Pipeline
Revision 只冻结 `KnowledgeContextBinding`，把 ACWM Artifact Slot 绑定到不可变
`RetrievalPolicyRevision`。TeamTemplate 不包含 Knowledge Stage、Provider 或查询顺序。本边界
沿用 ADR-0014：ACWM 拥有 Contract 语义，产品 Published Pipeline 拥有不可变发布身份和 Binding。
当前锁定 ACWM 若尚未声明该 Contract，必须先在 ACWM 仓库发布并锁定相应 Revision/Contract Hash；
产品不得在本地 Stage DTO 中复制一套同名输入语义。

### 不可变派生索引

`KnowledgeIndexStorage` 采用 `immutable-derived-index-v1`：

- 产品 SQLite 只保存 Index Revision 元数据、Hash、状态与 Active Pointer；
- Snapshot 正文进入 Content-addressed Artifact Store；
- `index_profile_revision_id` 引用不可变 `KnowledgeIndexProfileRevision`，只冻结会改变持久化索引内容的
  Content Normalizer、Chunker、Lexical Analyzer、Index Schema 和 Embedding Qualification Binding；
  纯查询期行为由 `RetrievalPolicyRevision` 唯一拥有，不改变 Partition Identity；
- `KnowledgeIndexPartitionKey=(provider_binding_id,index_profile_revision_id)`，Active Pointer
  以该键分区；多个 Project 可以复用 Binding Index，但不共享授权决定；
- 每个 Index Revision 冻结排序后的 `SourceSnapshotSetManifest` 及其 SHA，明确本 Revision 的完整
  语料身份；同一 Partition 的 Snapshot 集发生变化时构建新 Revision；
- 每个 Index Revision 使用独立、不可变的 SQLite/`sqlite-vec` 派生文件；
- Knowledge Application 只依赖 `VectorIndexPort`，`sqlite-vec==0.1.9` 建表、序列化、过滤和
  cosine 查询由 Infrastructure Adapter 拥有；
- Published Index Profile 冻结 Block-aware Chunk 大小/重叠、Document/Chunk 上限与容量
  告警比例；达到告警阈值不阻塞既有 Index，超过上限的新 Build 留下失败 Revision
  并 Fail Closed；
- Shadow Build 通过完整性和查询探针后，以 CAS 激活；
- Active Index 不原地修改；跨平台部署默认从 Snapshot 重建；
- 只有新 Snapshot 或新 Index Profile 产生新 Index Revision；
- 索引不是业务事实，损坏不能影响 Project、Delivery、Evidence 或 Release 数据。

`ProjectKnowledgeSourceApprovalV1` 在 Retrieval 前编译为允许 Source Set。FTS 与
`VectorIndexPort` 必须在候选生成阶段应用该过滤；未授权正文或分数不能先离开 Index Adapter
再由 Application 层删除。

`EmbeddingQualificationSnapshot` 只冻结模型/Adapter 资格：模型 digest、实际维度、
Embedding Adapter Revision、Vector Index Adapter Revision/Engine Version、模型 Tokenizer/Input Contract、
Vector Normalization、distance metric 兼容参数和资格 Hash。`latest` 不能作为不可变身份，
系统不自动下载模型；漂移时 Fail Closed。

`RetrievalPolicyRevision` 是运行时查询行为的唯一权威，引用兼容 Index Profile，并冻结 Query
Normalizer、BM25/vector 候选数量、RRF、Top-K、Threshold、分数量化、稳定排序、空结果策略和
Context 选择预算。`RetrievalEvaluationPolicyRevision` 只冻结 Dataset、Metrics、通过阈值、硬件描述
与 Gate 算法，并引用被评测的 Retrieval Policy/Index Profile；它不得覆盖运行时参数。

### 持久化 Context Preparation

Delivery 先完成本地 Readiness Preflight，再取得 Project Lease、持久化 Delivery 和首个事件，然后
进入 `preparing_context`。创建时冻结 `KnowledgePreparationInputV1`：内容寻址
`ProjectDescriptionSnapshot`（或等价不可变 Artifact Reference）、Delivery Goal、Published Pipeline
Revision、Stage Path、ACWM Artifact Slot 和 Stage Responsibility。仅保存无法恢复正文的 Project
描述 Hash 不足以支持重启。Query 只由该输入与 `RetrievalPolicyRevision` 确定性生成，不依赖
planning 或下游 Stage 输出。

`KnowledgeContextPreparationRun` 采用：

```text
queued → leased/running → retry_wait → succeeded | failed | cancelled
```

它保存 Input SHA、Binding/Policy Hash、幂等键、Lease、Attempt 和逐 Stage 结果；进程重启后回收
超时 Lease。在事务外执行 Ollama/Index 调用；全部 Required Context Artifact 成功后才编译最终
`DeliveryExecutionSnapshot` 并进入 `planning`。

失败必须留下 Delivery、错误码和 Receipt；不得生成部分 Snapshot。`retry_wait` 只处理受 Policy
约束的瞬时故障；进入终态 `failed` 后，Operator Retry 默认创建新 Delivery，不改写失败运行。
Required Binding 失败会终止 Delivery；Optional Binding 只能生成明确的
`KnowledgeContextUnavailableReceipt`，不能把故障伪装成正常零命中。

### 冻结与撤权

`KnowledgeContextArtifactV1` 冻结 Query、Policy、Scope、Snapshot、Chunk、Index、
Qualification、Citation 和 Hash。Repair Loop 复用同一 Context，Agent 不能访问 Feishu 或 Active
Index。

`RevocationPolicy` 采用 `best-effort-revoke-v1`。各权威独立维护单调版本；
`KnowledgeAuthorizationStampV1` 冻结：

- Global Identity Policy Revision；
- Delivery Authorized Principal 的 Identity Authorization Version（status/global role）；
- Project Authorization Version；
- Membership/Bypass Authorization Component；
- 排序后的 Source Approval ID/Version；
- 排序后的 Connection ID/Authorization Version。

Membership/Bypass Component 是不允许空值的判别联合：
`membership:{membership_id,version}` 或
`administrator_bypass:{sentinel,receipt_id,receipt_sha256}`。Administrator bypass 不伪造 Membership，
其 `sentinel` 固定为 `administrator-bypass:no-membership:v1`。Admission 与结果接纳必须以冻结时的同一
分支重新解析，并一并复核管理员身份授权版本与 Bypass Audit Receipt。

Stamp 的内容 Hash 是 `authorization_epoch_hash`。协调层通过模块 Policy Port 重新解析 Stamp，
不建立跨模块共同写入的全局 Epoch：

- 新 Attempt Admission 和 WorkcellResult 接纳都重新解析并比较 Stamp；
- 撤权后尽力取消运行 Attempt；
- Stamp 不匹配的结果与临时 Artifact 被隔离，不进入后续 Agent、Candidate、Evidence 或 Release；
- 不承诺追回已经发送给模型的内容，也不把远端探测延迟描述为即时撤权。

Project Authorization Version 只表达影响所有 Principal 的 Project 生命周期或安全策略变化，不因
无关 Membership/Approval CRUD 递增；Authorized Principal 的 Membership 和实际使用的 Approval
分别由各自版本表达。Connection Authorization Version 只因身份、资格、启停或权限性状态变化递增，
不因 Sync 进度、诊断时间戳或内容 Revision 变化递增。

无关 Membership 变化不撤销现有 Delivery；Authorized Principal、相关 Approval、Connection 或
Project 授权状态的权限性变化必须改变 Stamp。最后成功权限探测超过
`KnowledgeFreshnessPolicyRevision.max_permission_probe_age` 时，新 Retrieval/Attempt Fail Closed。

### 外部内容

Feishu Chunk 以 `external-collaborative` Data Context 注入，不能成为 System/Developer
Instruction。Citation URL 仅供展示；Runtime Capability/Egress Policy 不允许 Agent 据此实时访问飞书。
Citation 证明来源与完整性，不证明内容安全、真实或可执行。

## 状态模型

```text
KnowledgeIndexRevision:
building → built → qualified → active → stale | superseded
         └→ failed       └→ failed

Delivery:
local readiness → persisted/preparing_context
→ planning | failed

KnowledgeSyncJob:
queued → leased/running → retry_wait
→ succeeded | failed | cancelled
```

`preparing_context` 是持久化、可观察状态；外部调用不得发生在创建 Delivery 的数据库事务内。

## 兼容与切片

- R1 Pipeline 不变，在无 Feishu/Ollama 环境继续运行；
- 旧 User Auth Binding 为 `legacy-user-auth/disabled-for-rag`；
- 旧字符串 Scope 为 `legacy-unverified`，不驱动自动 RAG；
- Gate A 完成 Project Access/同步/词法；Gate B 完成 Hybrid Retrieval；Gate C 才接入 Delivery；
- 三个 Feature Flag 全部通过前，不得把 v0.5.1 表述为完整自动 RAG。

## 验证

- ACWM Artifact Slot 与 Pipeline Binding 发布校验；
- Index Partition、召回前 Scope Filter、Shadow Build、CAS 竞争、损坏、模型漂移和跨平台重建；
- 100,000 Chunk 容量、有界 RSS、重启完整性校验与查询分位时延，并明确排除生产 SLA；
- Delivery 持久化先于外部调用、Preparation 幂等/重启/失败、Lease、Query Hash 和部分 Artifact 隔离；
- Authorization Stamp、Attempt Admission、撤权竞态、Citation 伪造和 Prompt Injection；
- R1 回归、Deterministic 与真实 Tenant/Ollama Live Gate 分离；
- Live Readiness 与 Live Gate/Release Report 分离，且 Receipt 不包含 Secret、Credential
  Reference 或 Repository URI；
- 内置 `codex-simulated-hermes` 不能通过 Live Provider Binding 检查，安装了但未被产品
  Dispatcher 调用的 Adapter 也不能通过；
- Major Release Report 满足 `FAIL=0`、`WARN=0`、`skipped=0`，否则保持
  `blocked/not_run`。

## 结果

可变飞书内容和向量运行时被编译为 Delivery 可审计的不可变 Artifact，而不是 Agent 的隐式上下文。
代价是增加 Preparation 状态、索引 Revision、资格和撤权门禁，但这些复杂度直接服务于恢复、权限和
证据边界。
