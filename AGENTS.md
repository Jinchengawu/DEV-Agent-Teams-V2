# DEV-Agent-Teams V2 工程规则

- 产品定位：Agent-Team-OS 是交付控制面，不是多 Agent 聊天界面。
- ACWM 保持为跨 Stage 控制面；不得在本仓库复制其 Runtime Contract。
- AgentScope 拥有 Stage 内通信和 Agent 组合。
- Hermes Instance 拥有 PM 和 Project Admin 角色智能。
- Codex 拥有隔离工作区中的受控代码执行。
- 产品代码拥有权限、候选验证、Verification、Approval 和 Apply 策略。
- 以竖向交付切片建设功能，并使用公共接口测试验证。
- 不得将 Deterministic Adapter 表述为 Live Agent 证据。
- 未经明确决策，不得从旧 DEV-Agent-Teams 仓库迁移代码。
- 每个主要产品版本必须在同一 Git Revision 上通过核心用户闭环的浏览器冒烟测试，以及
  Deterministic 与 Live Release Gate。Release Report 必须满足 `FAIL=0`、`WARN=0` 和
  `skipped=0`，否则不得视为已验收。
- 每次主要版本交接必须提供独立的本地评测账号；密码不得进入 Git、应用日志、Fixture、
  Gate Report、截图或已提交文档。

## 语言与文档规范

- 文档产出中文优先。仓库文档、代码注释、CLI/API 描述和功能介绍默认使用简体中文。
- Canonical Identifier、API 名、错误码、命令及必要技术术语保留英文，不做会破坏契约的强制翻译。
- 需要英文文档时，中文文档保留在默认路径，英文版使用明确的 `.en.md` 伴随文件，不得替换中文默认文档。
