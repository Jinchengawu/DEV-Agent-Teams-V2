export type Delivery = {
  id: string; user_request: string; status: string; version: number;
  planning_identity: string; execution_identity?: string;
  journey_revision_id?: string; resolved_journey_sha256: string;
  requirements?: unknown; task?: { title: string; acceptance_ids: string[] };
  candidate?: { unified_diff: string; candidate_revision: string; diff_sha256: string; changed_files: string[] };
  verification?: { commands: string[]; exit_code: number; log_sha256: string; status: string };
  apply_receipt?: unknown; plan_gate?: Gate; candidate_gate?: Gate; error_code?: string;
};
export type Gate = { subject_sha256: string; revision: number; decision?: string };
export type WorkItem = { id: string; title: string; column: string; version: number; acceptance_ids: string[]; execution_identity?: string; available_commands: string[] };
export type Instance = { id: string; name: string; runtime_type: string; enabled: boolean; version: number; credential_ref?: string; features: string[]; health: { status: string; identity?: string; latency_ms?: number; error_code?: string } };
export type Revision = { journey_id: string; revision: number; definition: { steps?: Array<Record<string, unknown>> }; binding_snapshot: Record<string, Record<string, unknown>>; fingerprint: string; published_at: string };
export type Draft = { id: string; name: string; definition: { steps?: Array<Record<string, unknown>> }; layout: Record<string, unknown>; version: number; validation_status: string; validation_errors: string[] };
export type Knowledge = { id: string; title: string; artifact_type: string; sha256: string; revision: number; content: string; sources: Array<{ source_kind: string; source_id: string; delivery_id?: string }> };

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "content-type": "application/json" }, ...init });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? `HTTP ${response.status}`);
  return body as T;
}
