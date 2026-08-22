# Agent-Team-OS Control Console

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

## Interaction rules

- Green means a verifier re-read an immutable source; never merely “completed.”
- Enabled controls execute a real command. Disabled controls explain the missing precondition.
- Errors name the failed fact and one repair action.
- Motion is limited to state transitions, drag previews, and opening inspectors.
- Keyboard focus is always visible; reduced-motion disables animated edges and transitions.

