import { useMutation, useQueryClient } from "@tanstack/react-query";
import { request } from "../../shared/api/client";
import { projectKeys, type Project, type ProjectCreate, type ProjectDetail } from "../../entities/project/api";
export { LEGACY_PROJECT_ID, projectKeys, projectPath, useProject, useProjectId, useProjects, type Project, type ProjectCreate, type ProjectDetail } from "../../entities/project/api";

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

export function useProvisionFullstackRepositories(projectId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}/repositories/provision-fullstack`, { method: "POST" }),
    onSuccess: async (detail) => {
      client.setQueryData(projectKeys.detail(projectId), detail);
      await client.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
    },
  });
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
