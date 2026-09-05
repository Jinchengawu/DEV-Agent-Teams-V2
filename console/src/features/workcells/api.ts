import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";

export type ProjectWorkcellTopology = components["schemas"]["ProjectWorkcellTopology"];
export type WorkspaceBinding = components["schemas"]["WorkspaceBinding"];
export type WorkspaceBindingCreate = components["schemas"]["WorkspaceBindingCreate"];
export type VerificationProfile = components["schemas"]["VerificationProfile"] | components["schemas"]["VerificationProfileV2"];
export type WorkcellRunTree = components["schemas"]["WorkcellRunTree"];
export type ReleaseHealthV2 = components["schemas"]["ReleaseHealthV2"];
export type ReleaseManifestV2 = components["schemas"]["ReleaseManifestV2"];
export type ExternalReleaseView = components["schemas"]["ExternalReleaseView"];

export const workcellKeys = {
  project: (projectId: string) => ["project-workcells", projectId] as const,
  delivery: (deliveryId: string) => ["delivery-workcells", deliveryId] as const,
  releaseHealth: (projectId: string) => ["release-health-v2", projectId] as const,
};

export function useProjectWorkcells(projectId: string) {
  return useQuery({
    queryKey: workcellKeys.project(projectId),
    queryFn: ({ signal }) => request<ProjectWorkcellTopology>(
      `/v1/projects/${encodeURIComponent(projectId)}/workcells`,
      { signal },
    ),
    enabled: Boolean(projectId),
    retry: false,
  });
}

export function useVerificationProfiles(projectId: string) {
  return useQuery({
    queryKey: ["verification-profiles", projectId],
    queryFn: ({ signal }) => request<VerificationProfile[]>(
      `/v1/verification-profiles?project_id=${encodeURIComponent(projectId)}`, { signal },
    ),
    enabled: Boolean(projectId),
  });
}

export function useSetVerificationProfile(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ workspaceId, expectedVersion, profileId }: { workspaceId: string; expectedVersion: number; profileId: string }) => request<WorkspaceBinding>(
      `/v1/workspace-bindings/${encodeURIComponent(workspaceId)}/verification-profile`,
      { method: "PUT", body: JSON.stringify({ expected_version: expectedVersion, verification_profile_id: profileId }) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
  });
}

export function useQualifyVerificationProfile(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ workspaceId, expectedVersion }: { workspaceId: string; expectedVersion: number }) => request<WorkspaceBinding>(
      `/v1/workspace-bindings/${encodeURIComponent(workspaceId)}/verification-profile/qualify`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
    onError: () => client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
  });
}

export function useCreateWorkspaceBinding(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorkspaceBindingCreate) => request(
      `/v1/projects/${encodeURIComponent(projectId)}/workspace-bindings`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
  });
}

export function useVerifyWorkspaceBinding(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ workspaceId, expectedVersion }: { workspaceId: string; expectedVersion: number }) => request<WorkspaceBinding>(
      `/v1/workspace-bindings/${encodeURIComponent(workspaceId)}/verify`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
  });
}

export function useActivateProjectTeam(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (expectedVersion: number) => request<ProjectWorkcellTopology>(
      `/v1/projects/${encodeURIComponent(projectId)}/team-activate`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) },
    ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: workcellKeys.project(projectId) }),
        client.invalidateQueries({ queryKey: ["projects"] }),
      ]);
    },
  });
}

export function useDeliveryWorkcellRuns(deliveryId?: string) {
  return useQuery({
    queryKey: workcellKeys.delivery(deliveryId ?? ""),
    queryFn: ({ signal }) => request<WorkcellRunTree[]>(
      `/v1/deliveries/${encodeURIComponent(deliveryId!)}/workcell-runs`,
      { signal },
    ),
    enabled: Boolean(deliveryId),
    refetchInterval: 1_000,
  });
}

export function useWorkcellArtifact(deliveryId: string, runId: string, sha256: string) {
  return useQuery({
    queryKey: ["workcell-artifact", deliveryId, runId, sha256],
    queryFn: ({ signal }) => request<components["schemas"]["WorkcellArtifactPreview"]>(
      `/v1/deliveries/${encodeURIComponent(deliveryId)}/workcell-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(sha256)}`, { signal },
    ),
    retry: false,
    gcTime: 0,
  });
}

export function useCancelWorkcellRun(deliveryId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, expectedVersion }: { runId: string; expectedVersion: number }) => request<WorkcellRunTree>(
      `/v1/workcell-runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: workcellKeys.delivery(deliveryId ?? "") }),
  });
}

export function useReleaseHealth(projectId: string) {
  return useQuery({
    queryKey: workcellKeys.releaseHealth(projectId),
    queryFn: ({ signal }) => request<ReleaseHealthV2>(
      `/v1/projects/${encodeURIComponent(projectId)}/release-health`,
      { signal },
    ),
    enabled: Boolean(projectId),
    refetchInterval: 2_000,
    retry: false,
  });
}

export function useExternalRelease(deliveryId?: string) {
  return useQuery({
    queryKey: ["external-release-v2", deliveryId ?? ""],
    queryFn: ({ signal }) => request<ExternalReleaseView>(
      `/v1/releases/${encodeURIComponent(deliveryId!)}`,
      { signal },
    ),
    enabled: Boolean(deliveryId),
    refetchInterval: 1_500,
  });
}

export function useResumeForward(projectId: string, deliveryId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<ReleaseManifestV2>(
      `/v1/releases/${encodeURIComponent(deliveryId!)}/resume-forward`,
      { method: "POST" },
    ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: workcellKeys.releaseHealth(projectId) }),
        client.invalidateQueries({ queryKey: ["deliveries"] }),
      ]);
    },
  });
}
