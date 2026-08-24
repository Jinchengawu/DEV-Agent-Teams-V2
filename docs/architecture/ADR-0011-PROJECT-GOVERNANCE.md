# ADR-0011：项目治理、工作区隔离与来源投影

状态：已接受  
日期：2026-08-24

## 决策

Agent-Team-OS 使用 Project Governance Deep Module 管理项目生命周期、内部工作区引用、项目交付租约和全局资源授权。Project 不是包含交付、证据、Wiki 与 Agent 的巨型聚合；其他模块只保存 `project_id` 或运行开始时冻结的 `ProjectExecutionSnapshot`。

```text
Project Governance
├── Project
├── ProjectWorkspace
├── ProjectDeliveryLease
├── ProjectPipelineBinding
├── ProjectDeploymentAccess
└── ProjectKnowledgeSource

Delivery
├── DeliveryRun.project_id
└── ProjectExecutionSnapshot

Projection
├── ProductEvent.project_id
├── WorkItem.project_id
├── EvidenceRecord.project_id
└── KnowledgeSearchHit.project_id
```

ACWM 不感知 Project；它继续解析已经冻结的 Pipeline、Provider 与 Assignment。Git Adapter 只接受产品生成的 `project:<id>` Workspace Reference 或 `projects/<id>` Repository Reference，不接受浏览器文件路径。

## 不变量

1. Project 以 `provisioning → active | provision_failed → provisioning` 和 `active → archived` 管理生命周期；本版本不恢复 archived Project。
2. 新 active Project 必须固定且仅有一个默认 `ProjectPipelineBinding`；修改默认值不影响已启动 Delivery。
3. 每个 Project 同时最多一个非终态 Delivery。租约在 Delivery 与首个事件创建前获取，只在 `completed/rejected/failed/cancelled` 持久化后释放；`applying` 恢复前不得释放。
4. `DeliveryRun.project_id` 与 `ProjectExecutionSnapshot.project_id` 必须一致。Product Event、Board 与 Evidence 的项目字段是不可变作用域/查询索引，不是第二个归属权威。
5. 每个 Project 使用独立 Bare Repo、Main 与 Candidate Ref。Provisioning 失败保持可审计记录并可幂等重试，不回退到 `backend-demo`。
6. archived Project 禁止新交付、绑定修改、项目 Wiki 修改、外部同步和 Workspace Reset；历史查询、导出与 Evidence 重新验证仍允许。
7. Wiki、Evidence 与 Provider Snapshot 保留各自权威语义。`KnowledgeSearchIndex` 是可重建 FTS 投影，不复制 Evidence 为可编辑 Wiki。

## 兼容与迁移

- 历史数据进入 `legacy-default`，旧 `workspace_id=backend-demo` 只在兼容入口映射到该项目。
- 新控制台仅提交 `project_id`，项目路由与 Query Key 均包含 Project ID。
- 原始旧 Delivery Snapshot、哈希、数据库备份和迁移动作继续保存在现有迁移审计表；不生成虚假的历史业务事件。
- 项目级 RBAC 不在本版本范围。当前身份权限仍是全局角色，因此“项目隔离”只表示数据、Git 和运行作用域隔离，不代表已实现项目成员授权。

## 验证

- Project Provisioning 失败/重试、Binding CAS、归档只读和租约冲突使用公共接口测试。
- Git 测试创建两个 Project Workspace，应用 pj1 Candidate 后验证 pj2 Main Revision 不变。
- 浏览器验收创建 pj1/pj2、访问四个项目工作区并切换 Project Switcher；网络请求必须携带对应 `project_id`。
- Ruff、严格 Mypy、Pytest、TypeScript、Vitest、生产构建和真实 Codex 规划门禁必须通过。
