# Offline standard evaluation baseline — 2026-08-24

This is a sanitized, versioned projection of local report
`43303b10-4558-456f-8015-2dbf026d7eb1`. The original immutable JSON/Markdown report remains in the
content-addressed local Evidence Ledger and is intentionally excluded from Git because it contains
full deployment snapshots.

This is a historical suite 1.2.0 baseline. Its cases were code-embedded and the repository does not
provide a replayable 1.2.0 dataset. Repeatable validation starts with versioned suite 1.3.0; 1.3.0
must not be described as an exact replay of this baseline.

| Property | Value |
|---|---|
| Suite | `agent-team-os-mvp` 1.2.0 |
| Profile | offline standard |
| Seed | 20260824 |
| Gate | `passed` |
| Proof scope | `fixture_harness_only` |
| Official benchmark | `false` |
| Evidence SHA-256 | `d9e2019fa6e86f632e0d3d513f04cf7a73d3de55065e05f845919080ead3e2c6` |

The run retained 600 observations after warm-up: ToolCall 300/300, General Agent 180/180, Data
Generation 60/60 ties, and Control Plane 60/60. Candidate HTTP latency was p50 2.36 ms, p95 6.29
ms and p99 9.43 ms. Candidate GraphRun total latency was p50 8.80 ms, p95 101.03 ms and p99 121.81
ms.

These results establish that the deterministic scoring harness and local control-plane probes were
operational. They do not establish live-agent intelligence, official BFCL/GAIA performance,
independent generation quality or production latency. See the adjacent JSON for machine-readable
denominators and exact values.

[Chinese default](2026-08-24-offline-standard.md)
