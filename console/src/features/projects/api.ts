import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";

export type Project = components["schemas"]["Project"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];

export const LEGACY_PROJECT_ID = "legacy-default";

export const projectKeys = {
  all: ["projects"] as const,
  detail: (projectId: string) => ["projects", projectId] as const,
};

export function useProjectId() {
  return useParams<{ projectId: string }>().projectId ?? LEGACY_PROJECT_ID;
}

export function projectPath(projectId: string, section = "overview") {
  return `/projects/${encodeURIComponent(projectId)}/${section}`;
}

export function useProjects() {
  return useQuery({ queryKey: projectKeys.all, queryFn: () => request<Project[]>("/v1/projects") });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: projectKeys.detail(projectId),
    queryFn: () => request<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}`),
    enabled: Boolean(projectId),
  });
}

export function useCreateProject(onCreated: (projectId: string) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProjectCreate) => request<ProjectDetail>("/v1/projects", { method: "POST", body: JSON.stringify(payload) }),
    onSuccess: async (detail) => {
      await client.invalidateQueries({ queryKey: projectKeys.all });
      client.setQueryData(projectKeys.detail(detail.project.id), detail);
      onCreated(detail.project.id);
    },
  });
}

export function useRetryProjectWorkspace(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}/workspace/retry`, { method: "POST" }),
    onSuccess: async (detail) => {
      client.setQueryData(projectKeys.detail(projectId), detail);
      await client.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

export function useResetProjectWorkspace(projectId: string) {
  return useMutation({ mutationFn: () => request<{ main_revision: string }>(`/v1/projects/${encodeURIComponent(projectId)}/workspace/reset`, { method: "POST" }) });
}

export function useArchiveProject(projectId: string, expectedVersion: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<Project>(`/v1/projects/${encodeURIComponent(projectId)}/archive`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: projectKeys.all }), client.invalidateQueries({ queryKey: projectKeys.detail(projectId) })]);
    },
  });
}
