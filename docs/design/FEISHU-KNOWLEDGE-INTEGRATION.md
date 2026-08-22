# 飞书知识库混合集成实施计划

## 目标

以一个可回滚的 PoC 验证以下闭环：

```text
管理员配置飞书应用凭据引用
→ 绑定一个飞书知识空间
→ 同步目录与一份文档快照
→ 生成本地 SHA-256 和全文索引
→ 在知识中心嵌入官方 Docs Component
→ 修改飞书文档后可观察同步与哈希变化
→ 撤销权限后本地标记不可用
→ Delivery Evidence 全程不受影响
```

## 范围分层

### 主架构线程

1. 冻结 `KnowledgeProvider` Port、Provider DTO 和错误码。
2. 新增 Provider Binding、Snapshot、Sync Run 与 Webhook Receipt 迁移。
3. 实现凭据引用、签名验证、重放防护、权限交集和事件幂等。
4. 实现 Feishu Gateway，不向 Domain 暴露 SDK 类型。
5. 定义 Docs Component 短时授权与同源嵌入策略。
6. 建立不可覆盖 Evidence 的架构测试。

### Spark 批量线程

在上述合同冻结后才派发：

- Provider 设置表单和中文诊断状态。
- 知识空间、目录和同步历史列表。
- Docs Component 容器、加载、授权失效和不可用状态。
- 本地快照、哈希、来源与关联 Delivery 的元数据面板。
- 定时同步、手工同步和 Webhook 结果的界面测试。

## 预定公共接口

PoC 冻结前不得由 Spark 自行调整：

```text
GET/POST /v1/knowledge/providers
POST     /v1/knowledge/providers/:id/diagnose
GET/POST /v1/knowledge/provider-bindings
POST     /v1/knowledge/provider-bindings/:id/sync
GET      /v1/knowledge/provider-bindings/:id/nodes
GET      /v1/knowledge/provider-documents/:id/snapshot
POST     /v1/knowledge/provider-documents/:id/embed-grant
POST     /v1/integrations/feishu/events
```

所有修改接口使用 Session、CSRF、RBAC 和稳定 Problem Detail。应用凭据只接受
`env:` 或 `keychain:` 引用。

## PoC 门禁

- 未配置飞书时，本地 Wiki、Delivery 和 Evidence 全部正常。
- 不同飞书用户不能通过本地 API 绕过外部文档权限。
- 同一 Provider Revision 重复同步不产生不同快照。
- 内容变化必须产生新哈希与 `knowledge.document-synced` 事件。
- 权限撤销后不再返回嵌入授权，也不向 Agent 检索暴露快照。
- 伪造签名、过期事件和重放事件均被拒绝且不修改投影。
- 飞书文档永远不能覆盖 `source_kind=delivery-evidence` 的本地记录。
- 测试不得把模拟 Provider 标记为真实飞书证据。

## 实施顺序

1. 完成本地 Wiki HTTP/UI 黄金纵切。
2. 完成 Provider Port 和确定性 Fake Provider 的合同测试。
3. 完成真实飞书单空间、单文档 PoC。
4. 完成权限撤销、Webhook 幂等与失败恢复。
5. 完成前端嵌入和本地来源面板。
6. PoC 通过后再决定是否将手工 Markdown 编辑降级为离线备选。

