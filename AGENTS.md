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
- Every major product version must pass a browser smoke test of its core user flow and the
  deterministic plus live release gates on the same Git revision. A release report must contain
  `FAIL=0`, `WARN=0`, and `skipped=0`; otherwise the version is not accepted.
- Every major-version handoff must provide a dedicated local evaluation account. Keep its password
  out of Git, application logs, fixtures, gate reports, screenshots, and committed documentation.
