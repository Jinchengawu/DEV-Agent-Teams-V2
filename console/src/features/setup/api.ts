import { useQuery } from "@tanstack/react-query";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";

export type SetupReadinessReport = components["schemas"]["SetupReadinessReport"];

export const setupReadinessKeys = {
  root: ["setup-readiness"] as const,
  detail: (projectId?: string) => ["setup-readiness", projectId ?? "global"] as const,
};

export function useSetupReadiness(projectId?: string, enabled = true) {
  return useQuery({
    queryKey: setupReadinessKeys.detail(projectId),
    queryFn: ({ signal }) => request<SetupReadinessReport>(`/v1/setup-readiness${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, { signal }),
    enabled,
    retry: false,
  });
}
