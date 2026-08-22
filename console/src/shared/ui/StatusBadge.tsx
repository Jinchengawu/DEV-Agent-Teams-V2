import { statusLabel } from "../../i18n";

export function StatusBadge({ value }: { value: string }) {
  return <span className={`status status-${value.replaceAll("_", "-")}`}>{statusLabel(value)}</span>;
}

