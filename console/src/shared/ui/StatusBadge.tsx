import { Tag } from "antd";
import { statusLabel } from "../../i18n";

export function StatusBadge({ value }: { value: string }) {
  return <Tag color={statusColor(value)} bordered>{statusLabel(value)}</Tag>;
}

function statusColor(value: string) {
  if (["ready", "active", "completed", "passed", "verified", "valid", "qualified", "healthy"].includes(value)) return "success";
  if (["failed", "rejected", "cancelled", "invalid", "failed_cancelled", "provision_failed", "unhealthy"].includes(value)) return "error";
  if (["awaiting_plan_decision", "awaiting_candidate_decision", "plan_approval", "candidate_approval", "warning"].includes(value)) return "warning";
  if (["planning", "executing", "verifying", "applying", "running"].includes(value)) return "processing";
  return "default";
}
