# Agent-Team-OS UI/UX 二次整改与复验交接

> 面向开发者 Agent 的第二轮整改合同。本文承接
> [`2026-09-19-UI-UX-AND-DELIVERY-BLOCKERS.md`](2026-09-19-UI-UX-AND-DELIVERY-BLOCKERS.md)，
> 记录 PR #20 合并后的剩余缺口、回归保护和下一次验收所需证据。
> 本文不构成产品 Gate、Release Gate、Qualification、Approval 或 Apply 授权。

## 0. 状态边界

| 字段 | 当前状态 |
|---|---|
| 文档日期 | 2026-09-20 |
| 评估基线 | `main@90b7dd12ba3dfff197159d029c828806d34473b0` |
| 合并来源 | PR #20；候选提交 `60f724217e929535ce2013a4c78f463bdc6d6b5e` |
| 远端状态 | 复核时 `main == origin/main` |
| 当前阶段 | Acceptance 后的二次整改交接 |
| 产品验收建议 | **Rejected / 尚不完整** |
| 授权决定 | None；没有授权人在本文中批准任何 Gate 或 Apply |
| 下一评审者 | 产品所有者 / 验收 Agent |

## 1. 结论

PR #20 已修复第一轮六项 P1 UI 问题中的大部分布局与可达性缺陷，但严格阶段验收仍不完整：

1. `UI-005` 的隐藏裁切已消除，且已有动作摘要和章节导航；但移动端 Delivery Detail 仍约
   `16,868px` 高，长正文、历史记录和低优先级证据没有形成真正的按需阅读层级。
2. 移动导航已经在 `390px` 真实浏览器上下文中通过功能走查，但原验收合同要求的 `620px`、
   `860px`、键盘焦点闭环和 `aria-current` 仍缺独立证据。
3. Team 拓扑已经改用主题语义 Token，并在移动端提供替代列表；可读性问题已明显收敛，但尚无
   WCAG AA `4.5:1` 的正式对比度测量。
4. 第一轮 P2 问题 `UI-007` 至 `UI-010` 尚未进入实现范围。
5. 四仓 Qualification、Feishu 权限新鲜度、Live Planning 超时和完整 Release 验收仍是独立交付
   工作流；本轮 UI 整改不得隐式执行这些写操作或把历史截图当作当前状态。

因此，本轮目标不是重新设计控制面，而是关闭剩余可观察缺口，并产出能够逐条判定的复验证据。

## 2. 证据分类

### 2.1 事实

- PR #20 已进入远程 `main@90b7dd1`；合并提交与候选提交的工作树内容一致。
- 候选树已通过前端 TypeScript typecheck、Production build，以及 `30 files / 102 tests`。
- `390×844` 浏览器走查中：移动导航包含全部九个入口、项目选择、账户身份和退出入口；
  Overview、Agents、Orchestration 均无页面级横向溢出。
- Agents 未显示的 Tab 位于明确的局部横向滚动容器中，没有继续撑宽整个页面。
- Delivery Detail 的 Evidence、输入框和 Diff 在直接以 `390px` 打开的移动上下文中未再越界。
- Team 移动端改为列表视图，浅色和深色主题下均能读取节点和 Artifact 链路。
- 第一轮复验没有批准 Gate、没有执行 Apply，也没有刷新外部 Feishu 或 Qualification 状态。

### 2.2 解释

- 第一轮整改已把主要问题从“功能入口不可达、关键控件被裁切”收敛为“信息层级与验收证据不足”。
- 自动测试和 `390px` 浏览器通过能够证明本地候选质量，但不能替代完整断点矩阵、可访问性审核、
  Live Release Gate 或真实用户结果。
- Delivery Detail 的像素高度不是独立产品指标；真正的问题是默认状态仍一次展开过多低优先级信息，
  操作者定位当前阻塞和下一步动作的成本仍然过高。

### 2.3 假设

- 本轮继续沿用现有导航信息架构，不新增页面或改变九个入口的产品归属。
- Delivery Detail 可以通过折叠、分页、按需加载或分段展开降低默认信息量，而不改变 Delivery、
  Gate、Verification、Approval、Apply 和 Receipt 的权威语义。
- P2 项可以拆成独立竖向切片，不需要等待外部 Feishu、Provider 或四仓 Qualification 恢复。

假设若与当前代码或产品决定冲突，开发者必须停止相应切片并请求产品所有者确认，不得自行扩大范围。

### 2.4 未知项

- `620px`、`860px` 下移动导航、Agents、Overview、Orchestration 和 Delivery Detail 的真实行为。
- 抽屉的完整键盘焦点顺序、`Esc` 关闭、关闭后焦点恢复和当前项语义。
- Team 浅色、深色主题的正式对比度数值。
- Team/Pipeline/Agent 写操作是否仍满足保存、校验、发布与权限合同。
- 当前四仓 Qualification、Feishu Binding、Live Planning 和 Release Report 的权威状态。

## 3. 二次整改范围与优先级

| ID | 优先级 | 整改切片 | 当前状态 | 建议责任域 |
|---|---:|---|---|---|
| R2-001 | P1 | Delivery Detail 默认信息层级与历史按需展开 | Required | Delivery / Frontend |
| R2-002 | P1 | 响应式断点矩阵与移动导航可访问性闭环 | Required | App Shell / Frontend |
| R2-003 | P1 | Team 双主题 WCAG 对比度验证与必要修正 | Required | Team / Design System |
| R2-004 | P2 | Knowledge Activity 分页、加载更多或虚拟化 | Open | Knowledge / Frontend |
| R2-005 | P2 | Board 移动端状态总览与 Lane 定位 | Open | Board / Frontend |
| R2-006 | P2 | 登录页移除已初始化环境的 `admin` 硬编码预填 | Open | Identity / Frontend |
| R2-007 | P2 | Settings 建立单一、可行动的发布异常摘要 | Open | Settings / Product UX |
| R2-REG-01 | P1 | PR #20 已通过行为的回归保护 | Required | Frontend / QA |
| R2-DEL-01 | 独立工作流 | DEL-001 至 DEL-004 权威状态重查 | Needs explicit authorization | Runtime / Integration / Release |

## 4. 切片 A：Delivery Detail 信息层级

### 4.1 用户问题

操作者虽然不再遇到隐藏裁切，但仍需要在超长页面中寻找“当前状态、阻塞原因、下一步动作和权限要求”。
这会增加误判 Gate、Verification、Apply 或 Release 状态的风险。

### 4.2 Must

- 首屏保留当前状态、阻塞原因、下一可执行动作和所需权限。
- 长请求正文、历史事件、完整 Diff、Evidence 明细和低优先级诊断默认折叠或分段加载。
- 折叠标题显示条目数量、当前异常数量或摘要，使用户不展开也能判断内容价值。
- 章节导航必须能到达所有主要区域，并在展开/收起后保持合理的阅读位置。
- 展开后的 Evidence、Diff、Receipt、输入控件在 `390px` 至桌面宽度均不得被裁切。
- Gate、Verification、Approval、Apply、Receipt 和 Release Manifest 继续显示为不同状态，不得合并成
  一个“完成”标志。

### 4.3 非目标

- 不改变 Delivery 状态机、Apply 语义、Receipt 格式或后端权威数据。
- 不通过删除证据、截断权威内容或隐藏错误来缩短页面。
- 不把某个固定像素高度写成新的产品合同。

### 4.4 验收条件

1. 使用真实长 Delivery 打开详情页，默认状态不一次展开完整历史和所有长正文。
2. 不展开低优先级章节时，可从首屏判断当前状态、阻塞和下一步。
3. 每个折叠章节可通过鼠标和键盘展开；展开后内容完整，收起后焦点与滚动位置可预测。
4. 在 `390px`、`620px`、`860px`、`1440px` 下页面级横向溢出均为 `0`。
5. 自动化测试覆盖默认折叠、展开、章节跳转和至少一个长 Evidence/Diff 场景。

## 5. 切片 B：响应式与移动导航可访问性

### 5.1 Must

- 在 `390px`、`620px`、`860px` 三个窄屏断点均可发现全部九个工作区入口。
- 当前项目、项目切换、用户身份和退出入口均可访问。
- 抽屉打开后焦点进入抽屉；`Tab` 顺序与视觉顺序一致；`Esc` 可关闭；关闭后焦点回到触发按钮。
- 当前路由使用可读取的选中态或 `aria-current`，不得只依赖颜色。
- 项目切换继续使用现有项目隔离路径，不改变业务状态机或权限边界。
- Agents 的局部 Tab 滚动、Orchestration 的局部画布滚动不得重新变成页面级横向溢出。

### 5.2 验收条件

1. 四档视口 `390×844`、`620×900`、`860×900`、`1440×900` 均形成浏览器报告。
2. 每档报告包含页面宽度、关键控件边界、可见入口、当前项、焦点闭环和 console error。
3. 通过可见 UI 从项目页进入 Agents、Teams、Orchestration、Settings；不得手输 URL 代替导航。
4. 关闭抽屉不触发项目切换、退出或其他业务写操作。

## 6. 切片 C：Team 双主题可读性

### 6.1 Must

- 对 Team 拓扑/列表中的标题、`workcell_key`、Workspace 类型和 Artifact 链路，在 light/dark 两种
  主题下读取 computed foreground/background，并计算对比度。
- 普通文字达到 WCAG AA `4.5:1`；大型文字如使用较低门槛，测试必须记录字号和字重依据。
- 节点、连线和 Artifact 类型不能只依赖颜色表达；保留文字、边框或图标等第二语义通道。
- `390px` 使用替代列表时，节点和链路信息完整，不重叠、不截断关键 Identifier。

### 6.2 验收条件

1. 提供 light/dark 两份机器可读对比度结果和对应截图。
2. 任一关键文本未达标则该切片失败，不得以“肉眼可读”豁免。
3. 桌面拓扑与移动列表表达相同 Workcell 和 Link 集合。

## 7. 切片 D：P2 可用性缺口

### R2-004 Knowledge

- Activity 使用分页、显式“加载更多”或虚拟化；不得继续把固定 `50` 条作为完整首屏。
- 支持按来源类型、Delivery、时间和项目范围缩小结果。
- 加载、空、错误、末尾四种状态可区分，展开摘要不丢失列表位置。

### R2-005 Board

- 移动端首屏能看到全部 Lane 名称、数量和异常提示。
- 支持折叠或切换 Lane，并能直接定位某个 Lane；空 Lane 不占据大段高度。
- 不改变合法状态命令和只读投影的边界。

### R2-006 Identity

- 已初始化环境的登录表单不再硬编码预填 `admin`。
- 首次 Bootstrap 的建议账号与正常登录状态分离，不显示其他用户信息。

### R2-007 Settings

- 相同根因只形成一个顶层摘要。
- 摘要至少说明影响、证据时间、下一步、所需权限和报告入口。
- `Unknown`、`Failed`、`Not run`、`Blocked` 使用不同语义。

## 8. 回归保护矩阵

| Criterion | 所需证据 | Evidence level | 通过条件 |
|---|---|---|---|
| 移动导航九入口 | 四档视口浏览器路径 | Local browser | 无隐藏入口；项目和账户操作可达 |
| Overview 响应式 | 宽度和控件边界报告 | Local browser | 页面横向溢出为 `0` |
| Agents 五个 Tab | 五 Tab 逐一打开 | Test + Local browser | 仅 Tab 条允许局部滚动；内容控件不越界 |
| Orchestration | 工具栏、依赖编辑、画布和 Inspector | Test + Local browser | 页面不横滚；画布局部滚动；语义不变 |
| Delivery Detail | 长 Delivery 默认态与展开态 | Test + Local browser | 不裁切；信息按需展开；状态边界清晰 |
| Team 双主题 | 对比度 JSON 与截图 | Automated accessibility + Local browser | 关键文字达到 AA；移动列表集合完整 |
| Knowledge / Board / Identity / Settings | 对应场景测试 | Test + Local browser | 满足第 7 节逐项条件 |
| Console/runtime 稳定性 | console、request、HTTP failure 记录 | Local browser | `console error=0`、关键请求无意外失败 |

所有浏览器证据必须来自同一 Git Revision。若测试后修改任何相关代码，原浏览器报告失效，必须重跑。

## 9. 实施顺序与停止条件

### 推荐顺序

1. `R2-001`：先关闭唯一仍未满足的 P1 产品结果——Delivery 信息层级。
2. `R2-002`、`R2-003`：补齐断点、键盘与 WCAG 证据。
3. 运行 `R2-REG-01`，确认 PR #20 已修复路径没有回归。
4. `R2-004` 至 `R2-007` 按独立竖向切片实现和复验。
5. UI 范围通过后，再由产品所有者决定是否授权 `R2-DEL-01` 的外部状态刷新与 Release 工作流。

### 停止与升级条件

- 需要改变 Delivery、Gate、Approval、Apply、Receipt 或 Release 状态语义时停止，并发起架构复审。
- 需要写入 Qualification、Feishu Binding、外部 Provider 或 GitHub Apply 状态时停止并请求明确授权。
- 响应式修复导致桌面端入口、权限或写操作变化时停止，不得用 UI 调整扩大产品能力。
- 无法在同一 Revision 取得测试和浏览器证据时，不得提交“验收通过”结论。

## 10. 架构审查

- **Architecture Impact:** `None`
- **Findings:** 当前整改限定在 UI 信息层级、响应式、可访问性与展示策略；不改变系统权威归属、
  Module/Port/Adapter 依赖、持久化、一致性、安全边界、Release 或 Apply 语义。
- **Required Revisions:** 无架构修订；实施中如触发第 9 节停止条件，必须重新审查。
- **ADR Required:** No。
- **Architecture Document Delta:** None；不得为本轮局部整改制造架构总览噪声。
- **Outcome:** `Approved`，仅表示本整改计划未发现架构阻断；**不是产品 Gate、Release Gate 或 Apply 批准**。

## 11. 下一次验收事件

开发者 Agent 完成整改后，应提交一个固定 Revision 的证据包，至少包含：

1. 修改文件和对应 `R2-*` ID；
2. typecheck、单元/组件测试和 Production build 结果；
3. 四档视口浏览器报告与关键截图；
4. Team light/dark 对比度机器读数；
5. Delivery 默认态、展开态、章节跳转和长内容回归证据；
6. console、HTTP 和 request failure 摘要；
7. 未完成项、Waiver 请求及其决策人——没有授权人则保持 `Pending/Rejected`；
8. 明确声明本轮是否执行过 Gate、Qualification、外部写入或 Apply。

下一次验收只判定上述 UI/UX 范围。除非产品所有者另行授权，不得把 UI 通过扩张为
Deterministic Gate、Live Gate、Release Report 或正式交付通过。
