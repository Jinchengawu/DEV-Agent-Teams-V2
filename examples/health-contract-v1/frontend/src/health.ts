export const HEALTH_STATUSES = ["ok", "degraded", "unavailable"] as const;
export type HealthStatus = (typeof HEALTH_STATUSES)[number];
export interface HealthResponse {
  status: HealthStatus;
  version: "health-contract-v1";
}

export function parseHealthResponse(payload: unknown): HealthResponse {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("HEALTH_RESPONSE_INVALID");
  }
  const value = payload as Record<string, unknown>;
  if (
    Object.keys(value).length !== 2 ||
    value.version !== "health-contract-v1" ||
    !HEALTH_STATUSES.some((status) => status === value.status)
  ) {
    throw new Error("HEALTH_RESPONSE_INVALID");
  }
  return { status: value.status as HealthStatus, version: "health-contract-v1" };
}
