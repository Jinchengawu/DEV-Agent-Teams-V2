---
title: Agent-Team-OS v0.5.1 飞书知识库与自动 RAG 集成计划（架构修订版）
document_kind: final-plan
status: Accepted/Not Implemented
implementation_state: deterministic_verified_live_blocked
product_target: v0.5.1
planning_baseline: cfe597c05b3b0c65af57bf12d14b7f802fe7899f
architecture_governance_revision: 84f9904cfe1cf729a187292e8e1f0e0b42b1c6a4
implementation_base_requirement: latest origin/main containing architecture_governance_revision
architecture_change: ARCH-20260902-03
last_reviewed: 2026-09-02
language: zh-CN
---

# Agent-Team-OS v0.5.1 飞书知识库与自动 RAG 集成计划（架构修订版）

> 本文是完成 Architecture Review 后的 Final Plan。复合架构变更状态仍为
> `Accepted/Not Implemented`；实现已经开始，但局部代码与 Deterministic 验证不表示已经接入真实飞书、
> 发布并锁定 ACWM 上游 Revision、下载并资格化真实模型或通过 Live Gate。

本文按 `origin/main@cfe597c05b3b0c65af57bf12d14b7f802fe7899f` 审计；该 Merge Revision 已包含
架构治理 Revision `84f9904cfe1cf729a187292e8e1f0e0b42b1c6a4`。实施开始前仍必须重新 Fetch
`origin/main`：若远端已经前移，则使用包含该治理 Revision 的最新远端 SHA，并在 Release Report
记录实际 Base SHA。建议实现分支为 `codex/v051-feishu-knowledge-rag`。

`7401fa281a201728fa3cc504daa05d3a724fa7c6` 是架构总览记录的运行时代码审计基线；
`84f9904cfe1cf729a187292e8e1f0e0b42b1c6a4` 是架构治理 Revision；
`cfe597c05b3b0c65af57bf12d14b7f802fe7899f` 是把该治理 Revision 合入 `origin/main` 的
规划基线。三者含义不得混写，也不得把文档 Merge 当成运行时能力实现。

## 0. 实现对账（2026-09-02）

- Gate A：Project RBAC、Tenant App Connection/Binding、Approved Source、可靠 Sync Job、不可变
  Snapshot、Source 级权限新鲜度以及受监管 Scheduler/Worker 已实现，并完成 Deterministic
  API/运行时/浏览器验证。Scheduler 使用 15 分钟稳定桶，Worker 并发 2、最多尝试 5 次，
  24 小时目录对账不拥有 Job 状态。
- Gate B：不可变 Hybrid Index、Embedding Qualification、`VectorIndexPort`、Retrieval/Evaluation
  Policy、Shadow Build、CAS 激活、RAG Preview 与 Citation Receipt 已实现，并完成
  Deterministic API/浏览器验证与 100,000 Chunk 开发机容量基准。
- Gate C：Context Preparation、Authorization Stamp、Attempt Admission、结果接纳、撤权轮询和 Citation
  Guard 已实现；ACWM `0.5.1@ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed` 已发布
  Stage Input Artifact Contract 并进入产品 dependency lock。R2 Pipeline、七个 Stage Context、
  五个 Workcell/Citation 和 ReleaseManifestV2 Deterministic 浏览器闭环已通过，干净 clone 可重放契约。
- 三个 Feature Flag 默认关闭，并强制 `Gate A → Gate B → Gate C` 依赖；无配置时 v0.5.0 路径不变。
- Release Acceptance V2 已实现只读 Build/Pipeline/Attempt/Knowledge/Workcell/Candidate/PR/
  Receipt/Manifest 交叉校验与内容寻址报告；R2 Deterministic 四仓 E2E 已获得
  `FAIL=0`、`WARN=0`、`skipped=0`，并验证 QA Validation、可自洽重算 Hash 的 Workcell
  Snapshot、Hermes Planning/Workcell Main Attempt Phase 和 Knowledge Stage Result 篡改 Fail Closed。这只是
  Deterministic 结构证据，不是 Live Report。
- 真实 Tenant App、真实 Ollama、Gate C Live Delivery 与同 Revision Live Release Report 尚未运行。
  因此 `ARCH-20260902-03` 不晋升为 `Implemented/Verified`。

## 1. Architecture Review

| 字段 | 结论 |
|---|---|
| Architecture Impact | `Critical` |
| Findings | 原计划混合了逐用户 Feishu ACL 与 Tenant App 身份、复制了 ACWM Stage Contract、在 Delivery 持久化前执行外部检索、将大向量索引放入产品状态库，并缺少统一 Project RBAC、细粒度撤权版本、可恢复 Query 输入及 Prompt Injection 边界。 |
| Required Revisions | 采用 Tenant Service Principal、完整 Project RBAC、ACWM Artifact Binding 与上游 Contract 前置、持久化 Context Preparation、内容寻址 Query 输入、不可变派生索引、best-effort revoke、外部协作内容信任等级和三阶段 Gate。 |
| ADR Required | 是；ADR-0016、ADR-0017、ADR-0018，并同步 ADR-0006、ADR-0008、ADR-0011，澄清 ADR-0014 的 ACWM/Published Pipeline 权威。 |
| Architecture Document Delta | `ARCH-20260902-03`，状态 `Accepted/Not Implemented`。 |
| Outcome | `Approved` |

审查通过只代表架构决策可以进入实施计划。任何能力只有在同一 Git Revision 完成代码、Migration、
公共接口测试、浏览器闭环和相应 Gate 后，才能从 `Accepted/Not Implemented` 晋升为
`Implemented/Verified`。

## 2. 目标、非目标与版本边界

### 2.1 目标

v0.5.1 建立以下闭环：

1. Administrator 使用企业自建应用的 Tenant App 身份连接已存在的飞书租户；
2. Administrator 将已验证 Wiki Space 或子树批准给指定 Project；
3. 持久化同步任务读取 Wiki 目录与 Docx Block，生成不可变 `ProviderSnapshot`；
4. 词法与本地向量索引构成可重建的 `KnowledgeIndexRevision`；
5. ACWM Stage 通过 Artifact Contract 声明知识输入，Published Pipeline 冻结检索策略绑定；
6. Delivery 进入可观察的 `preparing_context`，生成并冻结 `KnowledgeContextArtifactV1`；
7. Hermes、Workcell Main/Child 只能消费冻结 Artifact，并返回受产品验证的 Citation；
8. 来源撤销后阻止新 Attempt，并拒绝旧授权 Attempt 的结果进入正式交付。

### 2.2 非目标

- 不实现用户 OAuth、个人 Feishu ACL 交集或 Docs Component；
- 不提供飞书写回、双向同步、Webhook 或公网回调；
- 不处理 Sheets、Bitable、附件、图片 OCR 或嵌入资源；
- 不自动下载 Ollama 模型，不接外部 Embedding API 或外部向量数据库；
- 不让 Agent 直接访问飞书、扩大 Source Scope 或执行隐藏检索；
- 不把 RAG 描述为长期 Agent Memory；
- 不改变 v0.5.0 四仓 Workspace、Workcell 或 Forward-only Release 语义；
- 不把 Deterministic Fixture、Mock Provider 或本地索引测试表述为 Live Feishu 证据。

### 2.3 部署边界

首个版本只面向受控单机 Alpha：FileVault、受控系统账号和 `0700/0600` 文件权限属于部署前提。
该前提不等于应用级静态加密、多租户隔离或生产 SLA。

## 3. 唯一权威与依赖方向

| 领域事实 | 唯一权威 | 其他模块允许持有的内容 |
|---|---|---|
| Feishu Wiki/Docx 人工正文与上游 Revision | Feishu | 外部 ID、Revision、URL、不可变 Snapshot |
| 用户身份、全局 Capability | Identity Module | User ID 与授权结果 |
| ProjectRole、成员、Project Knowledge Source Approval | Project Governance | Project ID、Membership/Approval Revision |
| Journey、Stage、DAG、Gate、Loop、Artifact Contract 语义 | ACWM | 产品只保存编译结果、Artifact Slot 和运行投影 |
| 不可变发布 Revision、Provider/Policy Binding | Agent-Team-OS Pipeline Catalog | `Published Pipeline Revision`、`KnowledgeContextBinding` Snapshot |
| Provider Binding、Source Head、同步、Snapshot、Index、Retrieval 与 Citation | Knowledge Module | 内容寻址 Artifact Reference |
| Delivery 状态、Lease、Context Preparation | Delivery Module | 冻结的 Context Artifact Reference |
| Attempt Admission、取消、结果接纳 | Workcell Execution Module | `KnowledgeAuthorizationStampV1` 与 Citation ID |
| Evidence、Candidate、Approval、Apply | Agent-Team-OS | Knowledge 只能作为引用来源，不得覆盖 Evidence |
| 向量生成 | Ollama Adapter | `EmbeddingQualificationSnapshot`，不拥有权限或检索权威 |

强制依赖方向：

```text
ACWM ArtifactContract
        + Published Pipeline KnowledgeContextBinding
        + Project ApprovedSourceScope
        + Active KnowledgeIndexRevision
        → Delivery Context Preparation
        → immutable KnowledgeContextArtifactV1
        → AgentAttempt
```

TeamTemplate 不包含 Knowledge Stage、查询顺序、Provider、凭据或真实 Source。Knowledge Module
不得直接写 Delivery、Workcell、Evidence 或 Release Repository；通过 Application Interface、
Port、Artifact Reference 和已提交 Product Event 协作。

`KnowledgeProviderBinding` 与 `ProviderSourceHead` 属于 Knowledge Module；
`ProjectKnowledgeSourceApprovalV1` 属于 Project Governance。后者只引用 Binding/根节点标识和
冻结 Revision，通过 Policy Port 判断某 Project 可以使用哪些 Source；Knowledge Module 不拥有
项目成员政策，Project Governance 也不复制 Snapshot 或 Index。

## 4. 身份与 Project RBAC

### 4.1 Tenant App Service Principal

`FeishuAccessModel` 固定为 `tenant-service-principal-v1`：

- Connection 使用企业自建应用的 `app_id_ref`、`app_secret_ref` 获取 `tenant_access_token`；
- Secret 只接受 `env:`/`env://` 或 `keychain:`/`keychain://` Reference；
- API、SQLite、日志、Evidence、截图和 Gate Report 不得出现解析后的 Secret 或 Token；
- 不解析当前产品用户的 Feishu 身份，也不调用 User Access Token；
- 所有 Project 成员共享 Administrator 批准给该 Project 的来源；
- 控制台必须明确提示：产品访问权限可能不同于用户个人 Feishu 权限。

### 4.2 有效权限

```text
Effective Permission
= Global Role Capability
∩ ProjectRole Capability
∩ Resource Policy
∩ Approved Source Scope（仅知识资源）
```

新增 `ProjectMembership`，ProjectRole 为 `owner | editor | viewer`：

| 操作 | owner | editor | viewer | Administrator bypass |
|---|---:|---:|---:|---:|
| 查看项目资源与批准来源 | 是 | 是 | 是 | 是，必须审计 |
| 发起 Delivery、检索、请求同步 | 是 | 是 | 否 | 是，必须审计 |
| 管理项目成员 | 是 | 否 | 否 | 是，必须审计 |
| 启停已批准的 Project Source | 是 | 否 | 否 | 是，必须审计 |
| 扩大 Space/根节点批准范围 | 否 | 否 | 否 | 是，必须审计 |
| 管理 Connection/Binding | 否 | 否 | 否 | 是，必须审计 |

Project Access Policy 必须统一覆盖 Project、Delivery、Board、Evidence、Knowledge、
WorkcellRun、AgentAttempt、Artifact 和 Release API。不得只在 Console 隐藏入口，也不得只保护
`/knowledge/search`。

Canonical Project-scoped Resource Matrix：

| Resource Family | 必须受控的访问形式 |
|---|---|
| Project/Delivery/Workcell/Release | List、Get by ID、Command、SSE、Export |
| Board/Evidence/Artifact | Projection Query、Direct ID、Download、Reverify |
| Knowledge | Search、Activity、Derivation、Snapshot、RetrievalRun、Citation、正文 Inspect |
| Console | Route、Query Key、Cache、刷新恢复；只作为服务端授权的交互投影 |

固定不变量：

- 新项目创建者成为 Owner；
- 禁止删除或降级最后一个有效 Owner；
- Membership 修改使用 `expected_version` CAS；
- 禁用用户立即失去 Project Capability；Identity 的禁用命令必须先通过跨模块
  `ProjectOwnershipGuard`，若该用户是任一非 Legacy 活动项目的最后有效 Owner 则 Fail Closed，
  要求先完成 Owner 移交；
- archived Project 保持当前只读规则，禁止成员扩大 Scope 或发起同步；
- Administrator 只有在旁路 Membership 时才称为 bypass；所有 Administrator 治理操作都产生包含
  actor、project、resource、reason 的审计事件，且不能旁路 Approved Scope、资源状态或 Evidence 不变量；
- 除显式 Legacy 例外外，每个活动 Project 必须保留至少一个有效 Owner；`legacy-default` 不伪造
  Owner Membership，由 Administrator 旁路治理。

## 5. Provider、Scope 与可靠同步

### 5.1 模型

- `FeishuTenantAppConnection`：Secret Reference、Tenant 身份、资格状态和 Connection Authorization Version；
- `KnowledgeProviderBinding`：一个 Connection 对应一个已验证 Wiki Space；
- `ProjectKnowledgeSourceApprovalV1`：Project Governance 拥有的 `space` 或 `subtree` 批准记录，
  包含 Binding、根 Node Token、
  `include_descendants`、允许类型、Scope SHA、状态和 Revision；
- `ProviderSourceHead`：当前上游 Revision、Snapshot Reference、路径及
  `available | tombstoned | quarantined`；
- `ProviderSnapshot`：不可变正文、Block Anchor、Source URL、Revision 与 SHA-256；
- `KnowledgeSyncJob`：持久化任务、Lease、Attempt、重试与结果。

同一 Provider Revision 返回不同内容时必须隔离并 Fail Closed。删除、移出 Scope 或失权的节点
不能进入新 Search/RAG；历史 Snapshot 只按审计保留策略读取。

### 5.2 KnowledgeSyncJob

```text
queued
→ leased/running
→ retry_wait
→ succeeded | failed | cancelled
```

Job 至少保存：幂等键、Connection/Binding/Scope、requested_by、attempt_count、
lease_owner、lease_expires_at、next_attempt_at、last_error_code 和时间戳。

- Scheduler 只负责扫描与入队，不拥有任务事实；
- Worker 通过数据库 Lease 取得工作，重启后回收超时 Lease；
- 相同 Binding/Source/Revision 的抓取全局去重，Project 只负责授权 Scope；
- Editor 只能请求既有 Approved Scope，不得提交任意外部 Source ID；
- `401` 先刷新 Tenant Token；持续认证失败才降级 Connection；
- 单个 Source `403/404` 只隔离该 Source，不自动降级全部 Connection；
- `429/5xx` 使用有上限的指数退避、抖动和 `Retry-After`；
- 每 15 分钟增量扫描、每 24 小时目录校准是默认策略，不是不可变领域常量；
- 当前 `knowledge-sync-runtime-v1` 不新增内存队列或 Scheduler 状态表：15 分钟 UTC Bucket 进入
  `idempotency_key`，数据库唯一约束负责跨重启去重；目录校准周期由 Binding 的持久化
  `last_permission_probe_at` 推导。抓取成功以 Source Head 的 `permission_probe_at` 形成 30 分钟
  新鲜度证据，RAG 不再用 Binding 目录时间替代文档级权限证据；
- `KnowledgeFreshnessPolicyRevision` 的 v1 参考值为
  `max_permission_probe_age=30m`；最后一次成功权限探测超过阈值时 Source Readiness
  必须 Fail Closed，禁止新 Retrieval 和 AgentAttempt，并返回
  `KNOWLEDGE_PERMISSION_PROBE_STALE`；
- 首个版本只允许一个受监管 Worker Process；多实例调度后移。
- 当前 Worker 固定 `max_concurrency=2`、`max_attempts=5` 和五分钟 Lease；`retry_wait` 到期后由
  Supervisor 自动恢复，不要求用户重复调用同步 API。手动同步仍可在请求内执行一次，以保持
  Alpha Console 的即时反馈，但后续重试仍由同一持久化 Job 驱动。

真实实现前必须用最小只读权限验证 Tenant Token、Wiki Space、子节点和 Docx Blocks API。
PoC 失败时 Live 能力为 `blocked`，不得回退为用户 OAuth 或 Mock。

## 6. 不可变 Hybrid Index

### 6.1 存储边界

`KnowledgeIndexStorage` 固定为 `immutable-derived-index-v1`：

- 产品 `agent-team-os.sqlite` 只保存 Index Revision 元数据、Hash、状态和 Active Pointer；
- 原始/规范化 Snapshot 正文进入 Content-addressed Artifact Store；
- `index_profile_revision_id` 引用不可变 `KnowledgeIndexProfileRevision`，只冻结会改变持久化索引内容的
  Content Normalizer、Chunker、Lexical Analyzer、Index Schema 和 Embedding Qualification Binding；
  纯查询期行为由 `RetrievalPolicyRevision` 唯一拥有，不改变 Partition Identity；
- `KnowledgeIndexPartitionKey=(provider_binding_id,index_profile_revision_id)`；Active Pointer
  以该 Partition Key 为键，不以 Project 为键；
- 每个 `KnowledgeIndexRevision` 冻结排序后的 `SourceSnapshotSetManifest` 及其 SHA；相同 Partition
  中出现新 Snapshot 时构建新 Revision，不能仅凭 Active Pointer 推断语料身份；
- 每个 `KnowledgeIndexRevision` 使用独立、不可变的派生 SQLite/`sqlite-vec` 文件；
- 新 Index 先 `building`，完整性和查询探针通过后，以 CAS 切换 Active Pointer；
- Active Index 永不原地修改；只有新 Snapshot 或新 Index Profile 产生新 Index Revision；
- 跨平台移动默认从 Snapshot 重建，不将可写索引文件作为可移植业务事实；
- 索引损坏只影响派生能力，不得损坏 Project、Delivery、Evidence 或 Release 状态。

```text
building → qualified → active → stale | superseded | failed
```

### 6.2 Embedding 资格

Ollama 只通过 `EmbeddingPort` 使用。`EmbeddingQualificationSnapshot` 至少冻结：

- `model_name` 与 `/api/tags` 返回的精确 `model_digest`；
- 实际 embedding dimension；
- `truncate=false`；超出模型上下文时显式失败，不允许静默截断改变 Chunk 语义；
- Embedding Adapter Revision、模型 Tokenizer/Input Contract、Vector Normalization 与 distance metric
  兼容参数；
- Adapter 与资格 Hash。

禁止以 `bge-m3:latest` 作为不可变身份。系统不得自动 Pull 模型；模型缺失、digest 或维度漂移时
Hybrid Readiness 为 `blocked`。资格探针使用与索引、查询相同的模型和参数。

`sqlite-vec` 参考资格版本锁定为 `0.1.9`；Knowledge Application 只依赖
`VectorIndexPort`，建表、向量序列化、Scope Filter 与 cosine 查询均由 SQLite Adapter 拥有。
它必须同时通过目标 macOS ARM64 与 CI Linux
加载、查询、备份和“由 Snapshot 重建”测试。版本升级需要新的 Adapter Qualification，不得因
包管理器出现更新版本而静默替换。

### 6.3 检索策略

`KnowledgeIndexProfileRevision` 拥有 Heading/Paragraph/Block 感知 Chunking、确定性 CJK bigram、
英文/错误码/Canonical Identifier Token 保留和持久化 Index Schema。

`RetrievalPolicyRevision` 是运行时查询行为的唯一权威，必须引用兼容的
`KnowledgeIndexProfileRevision`，并冻结 Query Normalizer、BM25/vector 候选数量、RRF 参数、
Top-K、Threshold、score quantization、稳定同分排序、空结果策略及 Context 选择预算。查询 Hash、
命中 Chunk、原始分数、量化分数、排序和空结果原因全部进入 Retrieval Receipt。

`RetrievalEvaluationPolicyRevision` 只拥有 Dataset/Query Manifest、Metrics、通过阈值、目标硬件描述和
Gate 判定算法，并引用被评测的 `RetrievalPolicyRevision` 与兼容 Index Profile；它不拥有或覆盖任何
运行时检索参数。

一个 Binding 的 Index 可以被多个 Project 复用，但 Chunk 必须携带 Source/Path/Snapshot 标识。
Retriever 先将 `ProjectKnowledgeSourceApprovalV1` 编译为允许的 Source Set，再把该过滤条件传入
FTS 与 `VectorIndexPort`；未授权 Chunk 必须在候选生成阶段排除，不能先返回正文或分数再由
Application 层过滤。RRF 后的再次过滤只作为防御性校验。公共测试必须证明跨 Project Query 不改变
对方结果数量、分数、摘要或时序可见元数据。

当前 Deterministic 参考 Profile 冻结每 Chunk 1,200 Unicode 字符、150 字符重叠、5,000
Document 和 100,000 Chunk 上限，达到 80% 显示容量告警，超过上限的新 Build 留下
失败 Revision 并 Fail Closed。这些是当前 Published Profile 参考值，不是生产 SLA。

精确的 Chunk 大小、Top-K、RRF k 和容量上限必须先通过 Evaluation，再分别发布到 Index Profile 或
Retrieval Policy；不能把计划默认值伪装成已验证 SLA。Context Hash 的“可重复”只适用于相同
Snapshot、Index、Policy、Qualification 和量化规则，不宣称不同硬件/运行时下未经资格验证的浮点
结果绝对一致。

## 7. ACWM Contract 与 Delivery Context

### 7.1 契约权威

ACWM Stage Artifact Contract 声明是否需要 `knowledge-context-v1`。Agent-Team-OS 不新增第二套
Stage 输入语义；Published Pipeline Revision 只冻结：

```text
KnowledgeContextBinding
  stage_path
  acwm_artifact_slot
  retrieval_policy_revision_id
  required
  max_context_bytes
```

Pipeline 发布时必须确认 ACWM Slot、Policy Revision、Provider/Index Capability 和大小边界相容。
ACWM `0.5.1@ae46ea81a2795b4b6dd5c46ce8c271c68e98b9ed` 已实现、发布并通过契约测试的
`knowledge-context-v1` Artifact Contract；Agent-Team-OS 的 Gate C 通过 Framework/Dependency Lock
消费该 Revision/Contract Hash，干净 clone 可重放该上游契约。
Agent-Team-OS 不得通过给本地 Stage DTO 增加同名字段来模拟上游 Contract。
现有 `agent-workcell-delivery:R1` 不变；启用知识输入的新 Revision 采用 Feature Flag，在无飞书、
无 Ollama 环境中不得破坏 R1。

### 7.2 持久化准备

Canonical Naming：

- 流程名称：Delivery Context Preparation；
- 持久化实体：`KnowledgeContextPreparationRun`；
- Policy Identifier：`durable-preparation-v1`。

```text
Local Readiness Preflight
→ acquire Project Delivery Lease
→ persist Delivery + first Product Event
→ preparing_context + persist KnowledgeContextPreparationRun(queued)
→ leased/running
   ├→ retry_wait → queued
   ├→ failed/cancelled → Delivery failed/cancelled
   └→ succeeded（全部 Required Artifact 已冻结）
      → compile final DeliveryExecutionSnapshot
      → planning
```

Readiness Preflight 只做本地、快速、无副作用检查。Ollama、Index 查询或其他可能阻塞的调用不得发生在
Delivery/Lease 数据库事务内，也不得发生在 Delivery 持久化之前。

创建 Delivery 时先冻结 `KnowledgePreparationInputV1`，只包含创建前已经存在的事实：
内容寻址 `ProjectDescriptionSnapshot`（或等价不可变 Artifact Reference）、Delivery Goal、
Published Pipeline Revision、Stage Path、ACWM Artifact Slot 与 Stage Responsibility。只保存
Project 描述 Hash 而无法恢复其规范化正文不满足重启恢复要求。`RetrievalPolicyRevision` 依据该输入
确定性生成每个 Stage Query 及 Query Hash。v0.5.1 不允许 Query 依赖 planning 或任何下游 Stage
输出；需要运行中动态检索的 Pipeline 必须使用后续 Contract Revision。

`KnowledgeContextPreparationRun` 保存 `delivery_id`、Input SHA、Binding/Policy Hash、幂等键、
lease_owner、lease_expires_at、attempt_count、next_attempt_at、各 Stage 结果和错误码。幂等键为
`(delivery_id,input_sha256,knowledge_binding_hash)`。启动恢复扫描超时 Lease：没有完成外部调用的
Run 重新入队；已经写入且 Hash 匹配的 Stage Artifact 复用；不匹配或部分 Snapshot 不进入最终
`DeliveryExecutionSnapshot`。

准备失败时：

- Delivery 保留可审计失败状态、错误码和 Preparation Receipt；
- 不进入 `planning`；
- 不生成部分完成的 `DeliveryExecutionSnapshot`；
- 不释放 Lease，直到失败终态和事件同一事务持久化；
- `retry_wait` 只处理 Policy 允许的瞬时故障；进入终态 `failed` 后，Operator 重试默认创建
  新 Delivery，不改写失败 Delivery 的 Artifact 或 Snapshot。

`required=true` 的 Binding 失败时 Delivery 必须失败；`required=false` 只能生成带稳定错误类别的
`KnowledgeContextUnavailableReceipt` 并继续，不能把 Provider/权限/Index 故障伪装成
`hit_count=0`。正常检索无命中才是成功的空结果。

### 7.3 KnowledgeContextArtifactV1

Artifact 至少包含：

- Delivery、Stage、Query、Policy 和 Binding Hash；
- Approved Scope、`KnowledgeAuthorizationStampV1` 与 `authorization_epoch_hash`；
- Source Snapshot、Index、Embedding Qualification Revision；
- Chunk Title、Source URL、Block Anchor、正文与 Content SHA；
- lexical/semantic/RRF 的量化分数；
- Retrieval 时间、命中/空结果原因和整体 Artifact SHA。

Repair Loop 复用同一 Stage Context；新 Feishu Revision 只影响后续 Delivery。Hermes、
Workcell Main/Child/Reviewer 只读取冻结 Artifact，不允许直接访问 Feishu、Active Index 或其他
Workcell Repository。

### 7.4 best-effort revoke

`RevocationPolicy` 固定为 `best-effort-revoke-v1`：

1. 各权威独立维护单调版本：Global Identity Policy Revision、Delivery Authorized Principal 的
   Identity Authorization Version（status/global role）、Project Authorization Version、
   Membership/Bypass Authorization Component、每个 Source Approval Version、每个 Connection
   Authorization Version；
2. `KnowledgeAuthorizationStampV1` 冻结 project、authorized_principal、上述版本和排序后的
   Approval/Connection 元组；Membership/Bypass Component 是不允许空值的判别联合：
   `membership:{membership_id,version}` 或
   `administrator_bypass:{sentinel,receipt_id,receipt_sha256}`；其内容 Hash 是
   `authorization_epoch_hash`；
3. 新 AgentAttempt Admission 必须重新解析当前 Stamp 并与冻结 Hash 比较；
4. 撤权后尽力取消已运行 Attempt；
5. Provider 返回后、WorkcellResult 接纳前再次解析和比较 Stamp；
6. Stamp 不匹配时标记 `authorization_revoked`，隔离结果和临时 Artifact；
7. 被隔离内容不得进入 WorkcellResult、Candidate、Evidence、后续 Agent Context 或 Release；
8. 系统不承诺让模型忘记已经发送的内容，也不把远端 Feishu 权限探测延迟表述为即时撤权。

Stamp 由协调层通过各模块 Policy Port 解析，不新增一个可被多个模块同时写入的全局计数器。
`Project Authorization Version` 只因会影响所有 Principal 的 Project 生命周期或安全策略变化而递增；
它不得因无关 Membership 或 Source Approval CRUD 递增。Authorized Principal 的 Membership、实际使用的
Approval 和 Connection 分别由各自版本覆盖。Connection Authorization Version 也只因身份、资格、
启停或权限性状态变化递增，不因普通 Sync 进度、诊断时间戳或内容 Revision 变化递增。
无关成员的新增不会使正在运行的 Delivery 失效；Delivery Authorized Principal 被禁用/移除、
Project 归档等授权状态、其使用的 Approval 或 Connection 发生权限性变化时，Stamp 必须改变。
Administrator bypass 发起的 Delivery 不伪造 Membership：bypass 分支的 `sentinel` 固定为
`administrator-bypass:no-membership:v1`；后续 Admission 与结果接纳必须以同一分支重新解析，并同时
复核管理员身份授权版本、Bypass Receipt 和其余资源/来源约束。

本地 Scope 禁用和相关成员移除可以立即阻止新使用；飞书侧删除或 Tenant App 失权只有在同步或权限
探测后才能被产品发现。UI 和运维文档必须展示最后探测时间；超过
`max_permission_probe_age` 时 Fail Closed。

## 8. 外部内容信任与 Citation

所有 Feishu 内容标记为：

```text
KnowledgeTrustClass = external-collaborative
```

- Chunk 只能作为明确分隔、带来源的 Data Context；
- Chunk 内“忽略之前指令”“调用工具”“访问其他仓库”等文本不具有指令权威；
- Knowledge Artifact 不能扩大 Tool、Workspace、Provider 或 Source 权限；
- Citation URL 只作为展示元数据，Runtime Capability/Egress Policy 不允许 Agent 据此实时访问飞书；
- Agent 输出只允许引用其冻结 Context 中存在且 SHA 匹配的 Citation；
- `WorkcellResult.knowledge_citation_ids` 由产品验证，不接受 Agent 自报 Source；
- 日志和错误默认不记录完整正文；正文查看需要 `knowledge-retrieval:inspect`；
- 测试必须覆盖 Prompt Injection、Citation 伪造、跨 Project ID、直接 Snapshot ID 和跨 Workspace 诱导。

Citation 证明来源与内容完整性，不证明内容真实、安全或适合执行。

## 9. API、错误与 Migration

### 9.1 API 分组

最终 OpenAPI 是请求/响应 Schema 权威；本计划只冻结能力边界：

- Identity/Project：Project Membership CRUD、权限检查与审计；
- Provider：Connection、Diagnose、Binding、Space/Node Discovery；
- Project Knowledge：Approved Scope、Readiness、Sync Job/Run、Retrieval Preview；
- Delivery：Knowledge Context、Preparation 状态与 Citation；
- Evaluation：Retrieval Dataset、Report 与 Gate 输入。

所有修改使用 `expected_version` CAS。列表、直接 ID 查询、Activity、Derivation、Snapshot、
RetrievalRun 和 Artifact 下载必须复用同一 Project Access Policy。

建议在实现切片冻结以下稳定错误类别：

- `PROJECT_ACCESS_DENIED`、`PROJECT_LAST_OWNER_REQUIRED`；
- `KNOWLEDGE_SOURCE_SCOPE_DENIED`、`KNOWLEDGE_CONNECTION_DEGRADED`；
- `KNOWLEDGE_PERMISSION_PROBE_STALE`；
- `KNOWLEDGE_INDEX_NOT_READY`、`KNOWLEDGE_MODEL_QUALIFICATION_DRIFT`；
- `KNOWLEDGE_CONTEXT_PREPARATION_FAILED`、`KNOWLEDGE_CONTEXT_REVOKED`；
- `KNOWLEDGE_PROMPT_TRUST_VIOLATION`。

### 9.2 Migration

Migration 从 `0036` 开始，不修改 `0001–0035`。建议切分：

- `0036_project_memberships.sql`；
- `0037_feishu_tenant_provider_v2.sql`；
- `0038_knowledge_sync_and_index_revisions.sql`；
- `0039_knowledge_context_bindings.sql`。

兼容规则：

- 旧 ProviderActor/User Token Binding 保持历史可读，标记
  `legacy-user-auth/disabled-for-rag`；
- 不从旧 Credential Reference 自动推导 Tenant App Connection；
- Administrator 必须显式新建 Connection、Diagnose、Binding 和 Approved Scope；
- 旧自由字符串 `source_scope` 投影为 `legacy-unverified`，默认 `rag_enabled=false`；
- 有合法、仍存在 `created_by` 的项目可以显式 Migration 为 Owner；缺失身份时不得伪造；
- `legacy-default` 由 Administrator bypass 治理；
- 现有 Snapshot、Wiki、Evidence、Delivery、R1 Pipeline 与历史哈希保持可读；
- OpenAPI 与 Console Client 在同一变更集生成并通过 Drift Check。

管理员迁移闭环：

1. 保留 Legacy Binding，并以 `replaces_binding_id` 创建新 Tenant Connection/Binding；
2. 使用幂等 `migration_key` 完成 Diagnose、Approved Scope、首次 Sync 和 Readiness；
3. 全部成功后，以 CAS 将 Project Approval 切换到新 Binding，并写
   `KnowledgeBindingMigrationReceipt`；
4. 任一步失败都不修改 Legacy Provenance 或历史 Snapshot，新 Binding 保持未激活并可重试；
5. 激活后 Legacy Binding 继续只读，不自动删除；回退只切换尚未用于新 Delivery 的 Approval，
   已冻结 Delivery 永不改写。

## 10. Console 与可观测性

- Settings：Tenant Connection、Secret Reference、Diagnose、Binding、Ollama/Vector Readiness；
- Project：Membership、Approved Scope、同步策略、手动同步和错误恢复；
- Knowledge：目录、Snapshot History、Sync Job、Index Revision、Hybrid Search 和“在飞书打开”；
- Orchestration：只在 Stage Inspector 展示 ACWM Artifact Slot 与 KnowledgeContextBinding，
  不在 TeamTemplate 编辑 Stage；
- Delivery/Workcell：`preparing_context`、Query、Policy/Index/Model Hash、Chunk Citation、
  Authorization Stamp/Epoch Hash、Attempt 注入关系和撤权状态；
- Evaluation：Fixture 必须显著标识 `Deterministic Fixture`，不得包含真实 Secret 或正文。

每个异步状态必须有 loading、empty、blocked、retryable、terminal failure 和 historical success 形态。
项目级页面不能通过直接 URL 或缓存 Query Key 泄漏其他 Project 数据。

## 11. 三阶段实施 Gate

### Gate A：Project Access 与可靠同步

范围：

- ProjectMembership 与统一 Project Access Policy；
- Tenant Connection、Binding、Approved Scope；
- 持久化 KnowledgeSyncJob、不可变 Snapshot；
- Lexical Search；不启用自动 RAG。

验收：

- 全部 Project API 权限矩阵、最后 Owner、Admin bypass 审计和跨项目隔离；
- Secret 扫描、Connection/Source 故障分类、Lease 与重启恢复；
- 无 Feishu/Ollama 时 v0.5.0 既有功能不退化。

### Gate B：不可变 Hybrid Retrieval

范围：

- Embedding Qualification；
- Shadow Index Build 与 CAS Activation；
- CJK/BM25/vector/RRF；
- Retrieval Preview、Dataset、容量与性能基准。

Gate B 的第一个交付物必须是 Published `RetrievalEvaluationPolicyRevision`，冻结 Dataset
Manifest SHA、Query 集、`Recall@K`/zero-hit/error-rate 阈值、最大 p95 Latency、Peak RSS、
目标硬件描述、通过算法，以及被评测的 Retrieval Policy/Index Profile ID 与 Hash。阈值必须由基线
校准后显式提交，本文不虚构数字；Evaluation Policy 不拥有运行时参数，没有该 Policy 或机器不可
判定的 Gate 结果时，Gate B 不能通过。

验收：

- Index 可重建、Active 不原地修改、模型漂移 Fail Closed；
- 中文短词、Canonical Identifier、错误码与语义改写评测；
- 报告实际硬件、数据量和分位延迟，不宣称生产 SLA。
- 当前开发机实测（[完整报告](../evaluation/results/2026-09-02-knowledge-index-capacity-100k.md)）：
  Apple M1 Max / 32 GiB、100,000 Chunk × 1024 维 Deterministic Vector，
  Index 607,920,128 bytes，Build 11.321 s，Peak RSS 425,705,472 bytes，查询
  p95 605.115 ms；该数据不包含真实 bge-m3 生成时间，不是生产 SLA。

### Gate C：Delivery 自动 Context

范围：

- 上游 ACWM `knowledge-context-v1` Artifact Contract Revision/Hash、产品侧 Slot 与 Pipeline Binding；
- `preparing_context`、冻结 Artifact、Attempt Admission；
- Citation Validation、best-effort revoke、Prompt Trust Boundary；
- Console、浏览器闭环和 Live Gate。

验收：

- Planning 与 Design/Frontend/Backend/QA 使用可追踪 Citation；
- Repair 不重新检索，Agent 无 Feishu 网络权限；
- 撤权阻止新 Attempt，旧 Authorization Stamp 结果无法进入交付；
- R1 Delivery 在无 Feishu、无 Ollama 时继续运行。

Feature Flag 建议为：

- `feishu_tenant_sync_v1`；
- `knowledge_hybrid_index_v1`；
- `delivery_knowledge_context_v1`。

只有 Gate C 全部通过后，v0.5.1 才能标记完成。Gate A/B 的 Deterministic 结果不能被宣传为完整
自动 RAG。

## 12. 测试与 Release Gate

### 12.1 必测场景

- Global Role、ProjectRole、Resource Policy、Approved Scope 四层授权；
- 跨 Project Search、Activity、Derivation、Retrieval ID、Snapshot ID 和 Artifact ID；
- Administrator bypass 审计、最后 Owner、禁用用户与 archived Project；
- 同 Revision/同内容幂等，同 Revision/不同内容隔离；
- Job 去重、Lease 过期、进程重启、`401/403/404/429/5xx`；
- Source 删除、移动、失权、tombstone、quarantine，以及权限探测超过 30 分钟后 Fail Closed；
- 模型缺失、digest/维度漂移、Index 构建失败、CAS 竞争和跨平台重建；
- Index 召回前 Scope Filter，以及跨 Project 的数量、分数、摘要和时序不可见；
- Prompt Injection、Citation 伪造、正文日志泄漏和跨 Workspace 诱导；
- Context Preparation Input/Query Hash、幂等、失败、进程重启和失败 Delivery 审计；
- Authorization Stamp 相关/无关 Membership 变化、撤权竞态与结果隔离；
- Project/Connection 粗粒度版本不会因无关成员、同步进度或内容 Revision 误撤销 Delivery；
- R1 兼容、OpenAPI Sync、Console Query Key 隔离和三档响应式浏览器测试；
- 100,000 Chunk 容量测试必须报告真实资源，不能转化为生产 SLA。

### 12.2 证据分层

- Unit/Contract：只证明领域和 Adapter Contract；
- Deterministic：只证明固定 Provider、Fixture、Job、Index 和状态机；
- Live PoC：真实 Tenant App、受限 Wiki Space、Docx、Ollama 和权限撤销；
- Major Release：同一 Git Revision 的浏览器闭环、Deterministic Gate、Live Gate 与
  `FAIL=0`、`WARN=0`、`skipped=0` Release Report。

缺少真实飞书权限、目标 Space、Ollama 资格模型或完整四仓条件时，Live 只能标记
`blocked/not_run`，不得使用 Mock、Deterministic 或历史截图替代。

### 12.3 Live Readiness 与 Live Gate 分离

`agent-team-os knowledge-live-readiness --project-id <project-id>` 是一个失败关闭的只读投影：

- 它从现有领域权威汇总 Project/Team、四个独立 External Git Workspace、Published
  `provider-v1` Pipeline、七个 Knowledge Context Slot、Approved Feishu Source、Active Index、
  Published Evaluation、Ollama Qualification、Resolved Provider Binding、Runtime Identity、产品已接线
  Runtime Adapter 和 ACWM Framework Lock；
- 它不写领域状态、不创建 Delivery、不 Apply Candidate，也不保存 Secret、Credential
  Reference 或 Repository URI；
- ACWM editable Worktree 必须干净，且 `HEAD`、`framework-lock.json`、`pyproject.toml` 的
  精确版本/Git Revision 与 `uv.lock` 的实际解析结果一致，否则 Framework Check 失败；
- Runtime Readiness 必须实际执行 `hermes acp --check`；PATH 中存在 Hermes CLI 不足以证明 ACP
  Protocol 可由产品 Adapter 使用；
- `ready/not_run` 仅表示可开始 Live Gate；只有后续真实 Delivery 产生同 Revision、
  `FAIL=0`、`WARN=0`、`skipped=0` 的 Release Report，才是 Live 验收证据。
- 当前 Published 内置规划 Slot 仍是 `codex-simulated-hermes`。产品 Runtime Dispatcher 已接线
  `hermes.acp` Role Turn，但只在 Published Slot 精确冻结该 Adapter、Runtime Instance Version、
  Runtime Identity 与连接配置指纹时才可选择；`http.sync` 仍未接线。当前
  `live-provider-bindings` 与 `product-runtime-adapters` 因默认绑定不满足条件而继续 Fail Closed；
  “Hermes CLI/Adapter 已安装”不等于“Hermes Attempt 已由产品调度并产生 Live 证据”。

Readiness Receipt 写入 `$AGENT_TEAM_OS_DATA_DIR/reports/readiness/`，与发布门禁报告分目录保存。
Deterministic Gate C 必须额外验证该投影能读取四仓事实，但因
`managed-bare-git`/固定 Provider 不符合 Live 前置条件而不得升级为 Live 通过。

已经存在真实 `completed` Delivery 后，使用同一前置检查执行只读验收：

```bash
agent-team-os knowledge-live-gate \
  --project-id <project-id> \
  --delivery-id <completed-delivery-id>
```

- Readiness `blocked` 时退出码为 2，仅写 Readiness Receipt，不生成 Release Report；
- Readiness `ready` 后只验证既有 Delivery，不启动 Agent、不重新检索、不 Apply 且不修改领域状态；
- 通过/失败报告写入 `$AGENT_TEAM_OS_DATA_DIR/reports/release-v2/`，且仅
  `FAIL=0`、`WARN=0`、`skipped=0` 为 `passed`；
- Report 不保存 Secret、Credential Reference、Repository URI、知识正文或模型原始响应。

## 13. ADR、架构对账与完成定义

本计划由以下 ADR 解释，不复制它们的决策历史：

- ADR-0006：当前全局 Identity/RBAC；
- ADR-0011：当前 Project Governance；
- ADR-0016：目标 Project-scoped Authorization；
- ADR-0008：保留 Feishu 内容/Evidence 权威边界；
- ADR-0017：Tenant Service Principal 与可靠同步；
- ADR-0018：不可变索引、ACWM Binding、Delivery Context 和撤权；
- ADR-0013：Published Binding 驱动产品 Runtime Dispatch；Hermes ACP Role Turn 的空沙箱、工具拒绝、
  实例/配置指纹与结构化结果接纳边界；
- ADR-0014：ACWM Artifact Contract 与产品 Published Pipeline Binding 的权威分离。

实施期间，`ARCH-20260902-03` 保持 `Accepted/Not Implemented`。只有同时满足以下条件才能晋升：

1. 三个 Feature Flag 的代码、Migration、API 和 Console 均落地；
2. ADR 与实际实现完成 Architecture Reconciliation；
3. Project RBAC、同步、索引、Context、Citation 和撤权测试通过；
4. 同一 Revision 的 Deterministic 与 Live Release Gate 满足工程规则；
5. 缺少 Live 条件时保持 `blocked/not_run`，不得晋升。

## 14. 后移范围

- 逐用户 Feishu ACL、用户 OAuth 与 Docs Component；
- Webhook/Event Subscription；
- 多 Worker/多实例调度；
- 外部 Embedding/Vector Service；
- Sheets、Bitable、附件与 OCR；
- 长期 Agent Memory；
- 多租户与共享服务器生产部署；
- 对已经发送给模型的内容进行“撤回”；
- v0.6 已有 Workspace-Set、Delta Release、Manifest CAS、非 Git Workspace Adapter 等范围。

## 15. 外部契约参考

以下链接是 PoC 和 Adapter Qualification 的上游入口，核对日期为 2026-09-02；链接存在不代表
本产品已取得权限或通过 Live Gate：

- [飞书 Tenant App tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
- [飞书 Wiki Space 列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)
- [飞书 Wiki 子节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list)
- [飞书 Docx Blocks](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list)
- [Ollama Embed API](https://docs.ollama.com/api/embed)
- [Ollama Tags API](https://docs.ollama.com/api/tags)
- [Ollama bge-m3 模型页](https://ollama.com/library/bge-m3)
- [sqlite-vec Releases](https://github.com/asg017/sqlite-vec/releases)
- [sqlite-vec 跨平台可写数据库风险报告](https://github.com/asg017/sqlite-vec/issues/297)
