import { useEffect, useState } from "react";

const prefix = "agent-team-os.delivery-draft.";

export function useDeliveryDraft(projectId: string, fallback: string) {
  const key = `${prefix}${projectId}`;
  const [value, setValue] = useState(() => read(key) ?? fallback);
  useEffect(() => setValue(read(key) ?? fallback), [fallback, key]);
  useEffect(() => {
    try { window.localStorage.setItem(key, value); } catch { /* Draft persistence is best effort. */ }
  }, [key, value]);
  return [value, setValue] as const;
}

function read(key: string) {
  try { return window.localStorage.getItem(key) ?? undefined; } catch { return undefined; }
}
