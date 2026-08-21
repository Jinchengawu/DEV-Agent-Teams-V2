# DEV-Agent-Teams V2 engineering rules

- Product identity: Agent-Team-OS, a delivery control plane rather than a multi-agent chat UI.
- Keep ACWM as the cross-stage control plane; do not copy its runtime contracts into this repository.
- AgentScope owns communication and stage-local agent composition.
- Hermes instances own PM and Project Admin role intelligence.
- Codex owns controlled code execution in an isolated workspace.
- Product code owns permissions, candidate validation, verification, approval and apply policy.
- Build vertical delivery slices with public-interface tests.
- Never represent deterministic adapters as live-agent evidence.
- Do not migrate code from the legacy DEV-Agent-Teams repository without an explicit decision.

