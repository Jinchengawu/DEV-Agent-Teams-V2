# Agent-Team-OS UI/UX 缺陷与交付阻塞修复清单

> 面向开发者 Agent 的实现与复验交接文档。本文记录已观察到的问题、证据边界和可判定的退出条件；
> 它不是实现批准、Gate 批准、Apply 授权或 Release 成功声明。

## 0. 文档元数据

| 字段 | 值 |
|---|---|
| 文档日期 | 2026-09-19 |
| 当前基线 | `main@0fc48021848bea05105f64d23a725f4b60a8ebf1` |
| 远端对齐 | 本轮复核时 `main == origin/main` |
| 产品阶段 | Acceptance 后的缺陷修复交接 |
| 产品验收状态 | **Rejected / 不通过** |
| Release 状态 | 本地 Alpha；不得从本文推导正式 Release 已通过 |
| 主要受众 | Frontend、Runtime、Knowledge Integration、Release Engineering Agent |
| 当前授权边界 | 可阅读与实现经用户单独授权的修复；本文自身不授权外部写入、Gate 或 Apply |

## 1. 结论与修复目标

当前桌面端核心只读旅程可以用于内部评测，但完整 UI/UX 验收不通过。阻断项主要集中在：

1. 移动端缺失系统级导航、项目切换和退出入口；
2. Agents、Orchestration、Overview 与 Delivery Detail 在 390px 视口出现横向溢出或隐藏裁切；
3. Team 拓扑在浅色主题下不可可靠阅读；
4. Knowledge、Board 和 Delivery Detail 在真实数据规模下缺乏有效的信息压缩与定位机制；
5. 图 1 所记录的四仓 Qualification、Feishu 新鲜度、Live Planning 超时和正式 Release 验收仍需作为独立交付工作流处理。

本修复工作的目标不是“让截图更好看”，而是让操作者能够在不猜测、不输入隐藏 URL、
不依赖横向拖动页面的情况下完成控制面的发现、定位、判断和下一步操作。

### 1.1 产品验收建议

- **Recommendation:** Rejected；当前版本不满足完整多端 UI/UX 验收条件。
- **Authorized decision:** None；没有授权人在本文中批准或拒绝 Release Gate。
- **Decision owner:** 产品所有者 / 用户。
- **Waiver:** None。
- **Rollback trigger:** 不适用；本文没有执行发布或 Apply。

## 2. 状态与证据边界

### 2.1 已验证事实

- 在隔离数据副本上以 `1440×900` 与 `390×844` 两种视口走查了 Projects、Overview、
  Deliveries、Board、Knowledge、Evidence、Agents、Teams、Orchestration 和 Settings。
- 首次 Bootstrap、自动登录、桌面端退出和重新登录成功。
- 打开了真实历史 Delivery、Evidence Inspector 和全部五个 Agent 工作区 Tab。
- 数据副本包含 113 条 Delivery 和 418 条 Evidence；因此长列表结论不是空 Fixture 推断。
- 基线浏览器走查未观察到请求失败、HTTP 4xx/5xx 或 console error；补充主题与布局走查同样为
  0 条 console error。
- 浏览器无运行时错误不能证明 UX 可接受；下面的问题均有布局测量、页面行为或当前源码支持。

### 2.2 图 1 的证据等级

用户提供的图 1 是此前一次阶段性验收结果截图。它证明“当时观察并记录过这些阻塞”，但不是当前外部
Feishu、Provider、Qualification 或 Release Gate 状态的实时读回。因此第 5 节全部标记为
`Needs revalidation`，开发者不得把截图中的 `blocked`、超时次数或资格状态直接当作当前事实。

### 2.3 未执行事项

本轮验收没有执行：

- Team/Pipeline 保存、校验或发布；
- Agent 创建、部署或项目授权写入；
- Plan、Design 或 Release Gate 决策；
- Candidate Apply、Receipt 或远端 `main` 回读；
- 外部 Feishu 权限探测刷新；
- 四仓 Verification Profile 重新资格化。

以上能力在本文中只能标记为 `Unknown` 或 `Needs revalidation`，不能标记为通过。

### 2.4 当前验收矩阵

| Criterion | Evidence | Evidence level | Current result | Gap owner |
|---|---|---|---|---|
| Bootstrap、登录、桌面退出与重新登录 | 隔离运行时浏览器路径 | Local browser | Pass | — |
| Projects、Deliveries、Evidence 桌面只读路径 | 1440×900 页面与交互走查 | Local browser | Pass | — |
| 移动端系统导航、项目切换与退出 | 390×844 DOM/CSS 读回 | Local browser + Static | Fail | App Shell |
| Overview、Agents、Orchestration 移动布局 | 页面宽度与控件边界测量 | Local browser | Fail | Frontend feature owners |
| Delivery Detail 移动阅读与操作 | 真实长 Delivery 全页及控件测量 | Local browser | Fail | Delivery UI |
| Team 拓扑双主题可读性 | light/dark 截图与源码 | Local browser + Static | Fail | Team / Design System |
| Knowledge、Board 真实规模可用性 | 真实数据全页高度与渲染方式 | Local browser + Static | Fail | Knowledge / Board |
| Team/Pipeline/Agent 等写操作 | 未执行 | Missing | Unknown | Feature owners |
| 图 1 外部与 Release 阻塞 | 历史截图，未实时读回 | Historical only | Unknown / Needs revalidation | Runtime / Integration / Release |

## 3. 缺陷总表

| ID | 优先级 | 工作流 | 状态 | 缺陷摘要 | 建议责任域 |
|---|---:|---|---|---|---|
| UI-001 | P1 | 全局导航 | Open | 移动端隐藏系统目录、项目切换和账户操作 | App Shell / Frontend |
| UI-002 | P1 | Agents | Open | 390px 下页面宽 580px，Tab、表单与列表越界 | Agents / Frontend |
| UI-003 | P1 | Orchestration | Open | 390px 下页面宽 661px，关键编排控件和图被裁切 | Orchestration / Frontend |
| UI-004 | P1 | Project Overview | Open | 390px 下页面宽 467px，验证方案控件越界 | Project Governance / Frontend |
| UI-005 | P1 | Delivery Detail | Open | Evidence 与输入控件被隐藏裁切，页面高 18,939px | Delivery / Frontend |
| UI-006 | P1 | Teams | Open | 浅色主题拓扑出现深底深字及节点拥挤 | Team / Design System |
| UI-007 | P2 | Knowledge | Open | 首屏完整渲染 50 条 Activity，移动页高 20,966px | Knowledge / Frontend |
| UI-008 | P2 | Board | Open | 六个 Lane 在移动端纵向堆叠，页面约 5,090px | Board / Frontend |
| UI-009 | P2 | Identity | Open | 初始化后的登录页仍硬编码预填 `admin` | Identity / Frontend |
| UI-010 | P2 | Settings | Open | 发布状态重复且缺少单一、可行动的异常摘要 | Settings / Product UX |
| DEL-001 | P1 | Verification | Needs revalidation | 四仓 Qualification 在 Runner/工具身份变化后失效 | Runtime / Verification |
| DEL-002 | P1 | Feishu | Needs revalidation | Feishu Binding 权限证据新鲜度待刷新 | Knowledge Integration |
| DEL-003 | P1 | Live Planning | Needs revalidation | `gpt-5.6-sol / low / read-only / 120s` 曾连续超时 | Runtime / Provider |
| DEL-004 | P1 | Release Acceptance | Needs revalidation | 浏览器闭环、Deterministic、Live Gate 和零容差 Report 未共同通过 | Release Engineering |
| ENV-001 | 非缺陷 | Runtime | Confirmed in Figure 1 only | Python 3.11.1 满足 `>=3.11,<3.13`，不得仅因此升级 | No action |

## 4. 当前 UI/UX 缺陷明细

### UI-001：移动端缺失系统导航、项目切换与退出入口

**事实**

- `AppShell` 定义了四个项目工作区入口、五个系统入口、当前项目选择器和退出登录按钮：
  `console/src/app/shell/AppShell.tsx:11-23,59-67`。
- `console/src/design-system/product.css:756-763` 在 `max-width: 860px` 时隐藏
  `nav[aria-label="系统目录"]`、`.workspace-card` 和 `.system-state`。
- CSS 中存在 `.mobile-project-switcher` 的展示规则，但当前 JSX 没有渲染该组件。
- 390px 实测仅能看到交付工作台、交付看板、证据、知识中心；系统目录和账户区的 computed
  `display` 均为 `none`。

**解释**

这不是导航收缩，而是能力入口丢失。用户无法通过可见 UI 进入 Agents、Teams、Orchestration、
Settings，也不能切换项目或退出登录。

**复现**

1. 使用 390×844 视口登录。
2. 进入任意项目页面。
3. 检查页首导航、项目选择和账户操作。
4. 不允许通过手工输入 URL 规避问题。

**验收条件**

- 390px、620px、860px 三个宽度均存在可发现的系统导航入口。
- 用户可在不输入 URL 的情况下进入全部九个工作区入口。
- 当前项目可查看、可切换；账户身份和退出登录可访问。
- 当前路由、键盘焦点和 `aria-current` 状态清晰。
- 打开和关闭移动导航不会改变业务状态。

### UI-002：Agents 移动端整体横向溢出

**事实**

- 390px 实测 document width 为 580px，横向溢出 190px。
- `Provider 能力`、`Runtime Adapter` 两个 Tab 以及角色列表、表单输入框均有部分位于视口外。
- 页面组件实际使用 `.agent-workspace-tabs-v2`：`console/src/features/agents/AgentsPage.tsx:85`。
- 移动端收缩规则仍指向旧类名 `.agent-workspace-tabs`：
  `console/src/app/console-theme.css:339`。

**验收条件**

- 390px 下 `documentElement.scrollWidth == clientWidth`。
- Tab 可通过明确的局部滚动、换行或移动选择器访问，但不得撑宽整个页面。
- 五个 Tab 各自的卡片、输入框、Select 和主操作按钮全部位于视口内。
- Tab 键顺序与视觉顺序一致，选中态仍可辨认。

### UI-003：Orchestration 移动端关键控件与图被裁切

**事实**

- 390px 实测 document width 为 661px，横向溢出 271px。
- `审批 Gate`、`有限 LOOP`、主图分支条件和“添加依赖边”等控件越出视口。
- Graph 节点、Minimap、发布绑定列表和长 Hash 在移动端出现裁切或极端压缩。

**验收条件**

- 页面本身不产生横向滚动；只有图画布允许受控的平移/缩放。
- Node Forge、依赖编辑器、校验、保存、发布操作在 390px 下完整可见。
- 画布之外的长 Hash 必须换行、截断并提供完整值查看方式。
- 移动端能选中节点，并在 Inspector 中读取完整配置。
- 不得因响应式修复改变 Pipeline、DAG、Gate、Loop 或发布语义。

### UI-004：Project Overview 移动端控件越界

**事实**

- 390px 实测 document width 为 467px，横向溢出 77px。
- 四个“选择机器验证方案”控件右边界约为 422.7px，超出 390px 视口。

**验收条件**

- Overview 在 390px 下无页面级横向溢出。
- Pipeline、Deployment、Repository、Knowledge Source 与 Verification Profile 的名称和操作不会互相挤压。
- 长 ID 使用 `min-width: 0`、换行或省略展示，完整值仍可复制或查看。

### UI-005：Delivery Detail 隐藏裁切且缺少信息定位

**事实**

- 移动页 document overflow 报告为 0，但六个 Evidence 按钮实际宽约 470.4px，右边界约
  505.4px；外层容器将其裁切。
- 一个 textarea 宽约 573.6px，左边界约 -183.6px。
- 同一 Delivery 详情在桌面端约 12,272px 高，在移动端约 18,939px 高。
- 页面同时承载需求、任务合同、Gate、Diff、Verification、Evidence、Workcell、Knowledge Context、
  Release 和事件历史，但没有稳定的章节索引或折叠层级。

**解释**

用户最需要回答的“当前处于什么状态、为何失败、下一步做什么”被大量历史信息淹没。裁切被
`overflow:hidden` 掩盖后，比显式横向滚动更难发现。

**验收条件**

- 所有 Evidence 行、输入框、Diff 与 Receipt 在 390px 下不被裁切。
- 页面提供可键盘访问的章节导航、折叠或等效的信息分层。
- 首屏明确显示当前状态、阻塞原因、下一可执行动作及其权限要求。
- 长正文和历史记录按需展开；展开后仍保留当前位置。
- 不得把 Gate 通过、Verification 通过、Apply 或 Release Manifest 混成一个状态。

### UI-006：浅色主题 Team 拓扑不可读

**事实**

- `.team-topology-canvas` 和 `.topology-workcell` 使用固定深色背景：
  `console/src/styles.css:59`。
- 节点主标题没有在浅色主题下设置适配的前景色，实测出现深底深字。
- 390px 下四个 Workcell 节点拥挤，部分文本互相覆盖或被截断。
- 深色主题可读性明显优于浅色主题，说明数据本身存在，问题来自主题和布局。

**验收条件**

- 浅色与深色主题下普通文字对比度至少达到 WCAG AA 4.5:1。
- 390px 下节点名称、`workcell_key` 和 Workspace 类型可辨认，不互相重叠。
- 连线、节点和 Artifact Bus 的语义不只依赖颜色表达。
- 对不可容纳的拓扑使用受控缩放、局部滚动或替代列表视图，不得裁掉信息。

### UI-007：Knowledge Activity 首屏规模不可控

**事实**

- `console/src/features/knowledge/KnowledgePage.tsx:244-250` 固定请求 `limit=50`。
- `console/src/features/knowledge/KnowledgePage.tsx:442-470` 将返回数据一次性完整渲染。
- 真实数据副本下移动页面高度约 20,966px；此前一次截图约 22,551px。

**验收条件**

- Activity 使用分页、显式“加载更多”或虚拟化，不得首屏完整铺出 50 条记录。
- 用户能按来源类型、Delivery、时间和项目范围快速缩小结果。
- 展开完整摘要不会导致列表位置丢失。
- 加载状态、空状态、错误状态和“已到末尾”状态可区分。
- 具体首屏条数由产品确认；在确认前不得把任意数量硬编码成新的产品契约。

### UI-008：Board 移动端失去状态总览价值

**事实**

- 六个 Lane 在移动端改为纵向堆叠。
- 实测页面约 5,090px；空 Lane 仍保留较大高度。
- 每 Lane 已有限制与展开机制，但不能解决跨 Lane 定位成本。

**验收条件**

- 移动端首屏能看到所有状态名称、数量和异常提示。
- 用户可跳转到某个 Lane，并能折叠或切换 Lane。
- 空 Lane 不占用与活跃 Lane 等同的长区域。
- 合法状态命令和只读投影必须继续区分，响应式修复不得放宽状态机。

### UI-009：登录页硬编码预填 `admin`

**事实**

- `console/src/features/identity/AuthGate.tsx:68` 初始化用户名状态为 `admin`。
- 在已有 `v051-evaluator` 等非 admin 用户的系统中，退出后登录页仍显示 `admin`。

**验收条件**

- 已初始化系统的登录表单不硬编码假定账号。
- 可保持空值、使用浏览器密码管理器或明确标注的最近账号，但不得泄露其他用户信息。
- 首次 Bootstrap 的管理员建议值与正常登录状态分离。

### UI-010：Settings 缺少单一可行动的发布摘要

**事实**

- 走查中同时出现重复的“发布状态未知 / 缺少可解析报告”信息。
- 页面包含运行参数、实例、Release Gate 和绑定配置，但缺少统一的当前影响与下一步说明。

**验收条件**

- 相同根因只显示一次主摘要；各子模块可以链接回主摘要，不重复制造多个顶层告警。
- 摘要说明影响范围、证据时间、下一步动作、需要的权限及对应报告。
- `Unknown`、`Failed`、`Not run` 和 `Blocked` 使用不同状态与文案。

## 5. 图 1：历史交付阻塞项

本节不是 UI 缺陷列表。每项在执行前必须先查询当前权威状态；图 1 只提供历史线索。

### ENV-001：Python 3.11.1 是符合合同的环境，不是缺陷

**图 1 事实**：Python 3.11.1 满足仓库 `>=3.11,<3.13` 合同。

**开发约束**

- 不得把 Python 3.11.1 本身归因为 Qualification 失效。
- 只有出现可复现的解释器能力缺失，且仓库合同或正式决策变化后，才能提出版本调整。
- 需要区分“解释器版本问题”和“Runner、工具链、依赖身份变化”。

### DEL-001：四仓 Verification Profile Qualification 失效

**图 1 记录**：旧快照后 Verification Runner 经历多次正式变更，工具及依赖身份发生变化；
Fail-closed 拒绝旧资格属于预期行为。恢复需要调用写接口重新冻结 Qualification。

**执行前读回**

1. 查询四个 Repository 当前 Verification Profile、Qualification Hash、Runner Revision、工具与依赖身份。
2. 比较失效原因是否仍然成立。
3. 记录每仓需要重新资格化的精确 Subject 和预期版本。

**授权边界**

- 重新资格化会写入权威状态，本文不构成执行授权。
- 未获得明确授权时，只能输出拟写入对象和差异，不能调用写接口。

**验收条件**

- 四仓分别产生绑定当前 Runner、工具和依赖身份的新 Qualification。
- 每条资格都有当前 Revision、Subject Hash、时间和可回读记录。
- 任一仓失败时保持 Fail-closed，不得复用旧资格伪装通过。

### DEL-002：Feishu Binding 权限证据新鲜度待恢复

**图 1 记录**：需要调用外部 Feishu 并写入新的权限探测状态。

**执行前读回**

- 当前 Tenant Connection、Binding Revision、Space 范围、最后探测时间和错误码；
- 凭据引用是否可解析，但不得打印、记录或复制凭据值；
- 项目是否仍批准该 Binding 及 RAG 范围。

**授权边界**

- 外部 Feishu 调用和新探测记录属于外部/权威状态写入，必须单独获得授权。
- 不得用旧截图、旧 Ready 状态或本地 Fixture 替代当前权限探测。

**验收条件**

- 新鲜权限探测绑定当前 Connection、Binding Revision 和 Space Scope。
- 探测结果可回读，并明确成功、拒绝或范围变化。
- 失败时保留原始错误码和最小脱敏上下文，不落盘凭据。

### DEL-003：Live Planning 120 秒超时

**图 1 记录**：Live Planning 固定为 `gpt-5.6-sol`、`low`、`read-only`、120 秒；两次真实调用均超时。

**待诊断假设**

- Provider 首 token 或总响应延迟超过固定预算；
- Prompt/Context 体积、工具初始化或网络等待消耗预算；
- 当前超时只覆盖总时长，没有区分连接、首 token、无活动和总预算；
- 任务与 `low` reasoning 或只读工具策略不匹配。

这些都是待验证假设，不能直接作为修复结论。

**验收条件**

- 先产出可复现时间线：连接、首 token、活动间隔、完成或取消时间。
- 明确超时属于 Provider、Transport、Context、工具启动还是产品预算策略。
- 工程修复必须有针对性回归；若需要改变模型、reasoning 或 120 秒策略，必须提交正式策略评审。
- 不得以无限延长超时、移除取消或改用 Deterministic Adapter 伪装 Live 成功。

### DEL-004：正式 Release Acceptance 尚未闭合

**图 1 记录**：完整浏览器闭环、Deterministic + Live Release Gate，以及 `FAIL=0`、`WARN=0`、
`skipped=0` 的 Release Report 尚未共同通过。

**验收条件**

- 浏览器闭环、Deterministic Gate、Live Gate 和 Release Report 全部绑定同一 Git Revision。
- Report 必须同时满足 `FAIL=0`、`WARN=0`、`skipped=0`。
- Live 证据必须来自真实 Provider/外部依赖，不得用 Deterministic 结果替代。
- Gate 通过只证明对应 Gate；最终完成仍需要 Apply Receipt、远端 SHA 回读和 Manifest 状态。
- 本文不授权执行 Gate 或 Apply。

## 6. 推荐实施顺序

### Slice A：移动端可达性恢复（P1）

- UI-001、UI-002、UI-003、UI-004。
- 目标：先恢复用户能够到达并操作所有工作区的基本条件。
- 必须增加 390px、620px、860px 浏览器布局回归。

### Slice B：核心判断页面可读性（P1）

- UI-005、UI-006。
- 目标：交付详情能回答当前状态和下一动作；Team 拓扑在双主题和小屏下可理解。

### Slice C：真实数据规模与信息密度（P2）

- UI-007、UI-008、UI-009、UI-010。
- 目标：减少无边界长页、重复告警和错误默认值，但不改变领域状态机。

### Slice D：历史交付阻塞恢复

- 先只读重验 DEL-001 至 DEL-004。
- 将仍成立的阻塞拆成 Runtime、Feishu Integration、Provider 和 Release Acceptance 四条独立任务。
- 只有获得相应授权后才能重新资格化、刷新外部权限证据或运行 Gate；Apply 继续单独授权。

## 7. 开发与验证要求

### 7.1 最低静态与组件验证

```bash
pnpm --dir console typecheck
pnpm --dir console test
pnpm --dir console build
```

上述通过只证明相应静态/组件范围，不能替代浏览器和 Live 验收。

### 7.2 UI 布局回归

项目已有只读布局审计入口：

```bash
.venv/bin/python scripts/browser_ui_layout_audit.py \
  --url http://127.0.0.1:<port> \
  --project-id <project-id> \
  --delivery-id <delivery-id> \
  --screenshot-dir <output-dir> \
  --report <output-report.json>
```

修复后至少覆盖：

- 视口：390×844、620×900、860×900、1440×900；
- 主题：light、dark；
- 页面：Overview、Delivery List/Detail、Board、Knowledge、Evidence、Agents 全部 Tab、Teams、
  Orchestration、Settings；
- 断言：无页面级非预期横向溢出、无可聚焦控件位于视口外、移动系统导航可达、console error 为 0。

### 7.3 Release 验收边界

- 只读 Live 检查点不是正式 Live Release Report。
- 不得把 `npm build`、组件测试、HTTP 200 或 `ready` 换算成正式 Release 通过。
- 正式交接必须遵循 `docs/runbooks/DELIVERY-HANDOFF-EVIDENCE.md` 和
  `docs/runbooks/LIVE-DELIVERY-CHECKPOINTS.md` 的证据边界。

## 8. 架构审查

- **Architecture Impact:** None
- **Findings:** 本文只记录现状、修复切片和验收条件；不改变控制面权威、模块所有权、依赖方向、
  持久化、安全信任边界、Release/Apply 或外部集成策略。
- **Required Revisions:** 无架构修订。实现阶段若改变导航之外的权限模型、Knowledge 查询合同、
  Provider 超时策略或 Qualification/Release 语义，必须重新进行 Architecture Review。
- **ADR Required:** No
- **Architecture Document Delta:** None
- **Outcome:** Approved

该 Outcome 只批准“本文档可以作为修复交接输入”，不批准任何实现、Gate 或 Apply。

## 9. 假设、未知项与停止条件

### 假设

- 移动端属于支持范围，因为当前产品已经存在 600/620/860px 多组响应式规则。
- 当前隔离数据规模可代表系统使用一段时间后的重度场景。

若产品明确决策为 Desktop-only，应更新正式产品范围和 Release Criteria；在此之前不能用该假设关闭
移动端缺陷。

### 未知项

- 当前四仓 Qualification、Feishu Permission Probe 和 Live Planning 是否仍保持图 1 状态；
- 写操作后的成功、失败、并发冲突和刷新反馈；
- Safari、Firefox、真实移动设备和屏幕阅读器表现；
- UI-007 的正式首屏条数与分页产品决策。

### 停止条件

开发者 Agent 遇到以下情况必须停止并请求用户决策：

- 修复需要改变 RBAC、状态机、Gate Subject、Apply 或补偿语义；
- 需要调用外部 Feishu、重新资格化权威状态、改变 Provider/模型/超时策略；
- 需要执行 Gate、Apply、Force Push 或写入远端 `main`；
- 当前读回与图 1 或本文基线矛盾，且差异会改变修复方向；
- 修复只能通过隐藏错误、降低验证强度或扩大超时来获得“通过”。

## 10. 最终验收矩阵

| Criterion | Required evidence | Evidence level | Result required |
|---|---|---|---|
| 移动端全部工作区、项目切换与退出可达 | 四视口导航与键盘路径报告 | Browser / Local | Pass |
| 关键页面无非预期横向溢出或隐藏裁切 | DOM geometry + 全页截图 | Browser / Local | Pass |
| Delivery Detail 能快速定位状态、阻塞与下一动作 | 真实长 Delivery 走查 | Browser / Local | Pass |
| Team 拓扑双主题可读且不重叠 | 双主题截图 + 对比度测量 | Browser / Local | Pass |
| Knowledge 与 Board 在真实数据下可定位 | 真实规模数据走查 | Browser / Local | Pass |
| TypeScript、组件测试和生产构建 | 命令原始输出 | Test / Local | Pass |
| 图 1 四项历史阻塞完成当前读回 | 当前权威 API/外部状态证据 | Local / External | Pass or explicitly Blocked |
| 正式 Release 闭环 | 同 Revision Browser + Deterministic + Live + Report | Release evidence | `FAIL=0/WARN=0/skipped=0` |
| Apply 与远端状态 | Apply Receipt + remote SHA read-back | External | 仅在另行授权后验证 |

## 11. 下一验收事件

开发者 Agent 完成 Slice A 和 Slice B 后，先提交一个不含 Gate/Apply 的 UI 修复候选，并附：

1. 变更文件与缺陷 ID 映射；
2. 四视口、双主题浏览器报告；
3. TypeScript、组件测试、Build 原始结果；
4. 已知残余问题和未执行的写入边界；
5. 同一 Git Revision SHA。

产品侧据此进行第一次复验。Slice C 和 Slice D 不得用 Slice A/B 的通过结果自动关闭。
