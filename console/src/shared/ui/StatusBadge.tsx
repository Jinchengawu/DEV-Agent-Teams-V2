import { Tag } from "antd";
import { statusLabel } from "../../i18n";

export function StatusBadge({ value }: { value: string }) {
  return <Tag color={statusColor(value)} bordered>{statusLabel(value)}</Tag>;
}

function statusColor(value: string) {
  if (["ready", "active", "completed", "passed", "verified", "valid", "qualified", "healthy", "succeeded", "applied"].includes(value)) return "success";
  if (["failed", "rejected", "cancelled", "invalid", "failed_cancelled", "provision_failed", "unhealthy", "interrupted", "timed_out"].includes(value)) return "error";
  if (["awaiting_plan_decision", "awaiting_design_decision", "awaiting_candidate_decision", "plan_approval", "design_approval", "candidate_approval", "plan-approval", "design-approval", "candidate-approval", "warning", "release_drifted", "needs_attention"].includes(value)) return "warning";
  if (["planning", "executing", "verifying", "applying", "running", "delegating", "reviewing", "synthesizing"].includes(value)) return "processing";
  return "default";
}
