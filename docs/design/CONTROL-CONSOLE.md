# Agent-Team-OS Control Console

> v0.5 的核心视觉语言是“四个隔离 Repository Cassette 连接到一条内容寻址
> Artifact Bus”。这个拓扑表达组织和输入边界，不表达 Pipeline Stage 顺序。

## Subject and job

The console is an engineering delivery control room for an operator who must decide whether a
machine-produced code change is safe to apply. Its first job is to expose the next legal action and
the immutable evidence supporting it.

## Visual system

- `--ink-950 #06111d`: primary work surface.
- `--ink-900 #0a1928`: raised operational surface.
- `--line-700 #244158`: structural lines.
- `--cyan-500 #31a8ff`: selected control and navigation.
- `--teal-400 #43e6ce`: machine-verified evidence only.
- `--amber-400 #f4b84a`: human decision pending.
- `--red-400 #ff6577`: blocked or invalid evidence only.

Display text uses a condensed engineering stack (`DIN Alternate`, `Noto Sans SC`, `PingFang SC`).
Body text uses `Noto Sans SC`/`PingFang SC`; immutable data uses `IBM Plex Mono`/`SFMono-Regular`.

## Layout

```text
┌ navigation ┬ page command bar ──────────────────────────────────────┐
│            ├ active work / selected record ┬ evidence rail          │
│            │                                │                        │
│            ├ history / secondary workspace ┴ detail inspector       │
└────────────┴─────────────────────────────────────────────────────────┘
```

Deliveries and Board show the evidence rail because it encodes real running context. Orchestration,
Agents, Knowledge, Evidence, and Settings use their own task-specific workspace instead of a repeated
global progress banner.

## v0.5 三层控制面

1. **TeamTemplate**：编辑 Workcell 名称、职责、Primary Workspace 类型、Delegate Purpose、
   资源上限和 Artifact 拓扑。界面不出现 Stage、Provider、Credential 或 Release Member 字段。
2. **Project Workcell Setup**：每个 Workcell 独立绑定、验证 Repository，只接受间接
   Credential Reference。四仓全部 Ready 后才能 Team Activate。
3. **Delivery Execution**：展示 WorkcellRun、Main/Child/Attempt Tree、Method Snapshot Hash、
   Candidate/Verification/ReviewArtifact、PR、RemoteApplyReceipt、Manifest 和 Release Drift。

`Resume forward` 只在 `needs_attention` 且产品 Release Health 为 `release_drifted` 时可用；
控制台不提供回滚、Force Push 或 GitHub Merge 按钮。

## Interaction rules

- Green means a verifier re-read an immutable source; never merely “completed.”
- Enabled controls execute a real command. Disabled controls explain the missing precondition.
- Errors name the failed fact and one repair action.
- Motion is limited to state transitions, drag previews, and opening inspectors.
- Keyboard focus is always visible; reduced-motion disables animated edges and transitions.
