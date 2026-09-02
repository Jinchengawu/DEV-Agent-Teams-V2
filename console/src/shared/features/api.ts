import { useQuery } from "@tanstack/react-query";
import type { components } from "../api/generated/schema";
import { request } from "../api/client";

export type FeatureFlags = components["schemas"]["FeatureFlags"];

export const featureKeys = {
  current: ["feature-flags"] as const,
};

export function useFeatureFlags() {
  return useQuery({
    queryKey: featureKeys.current,
    queryFn: ({ signal }) => request<FeatureFlags>("/v1/features", { signal }),
  });
}
