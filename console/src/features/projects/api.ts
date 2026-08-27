import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import type { components } from "../../shared/api/generated/schema";
import { ApiProblem, request } from "../../shared/api/client";

export type Project = components["schemas"]["Project"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];

export const LEGACY_PROJECT_ID = "legacy-default";
export const ACTIVE_PROJECT_STORAGE_KEY = "agent-team-os.active-project";

export const projectKeys = {
  all: ["projects"] as const,
  detail: (projectId: string) => ["projects", projectId] as const,
};

export function useProjectId() {
  return useRouteProjectId() ?? LEGACY_PROJECT_ID;
}

export function useRouteProjectId() {
  return useParams<{ projectId: string }>().projectId;
}

export function projectIdFromPath(pathname: string) {
  const match = /^\/projects\/([^/]+)(?:\/|$)/.exec(pathname);
  if (!match) return undefined;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return undefined;
  }
}

export function readActiveProjectId() {
  try {
    return window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export function rememberActiveProjectId(projectId: string) {
  try {
    window.localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, projectId);
  } catch {
    // Storage only improves navigation. The URL remains the authority for project scope.
  }
}

export function projectPath(projectId: string, section = "overview") {
  return `/projects/${encodeURIComponent(projectId)}/${section}`;
}

export function useProjects() {
  return useQuery({ queryKey: projectKeys.all, queryFn: ({ signal }) => request<Project[]>("/v1/projects", { signal }) });
}

export function useProject(projectId: string) {
  return useQuery({
    queryKey: projectKeys.detail(projectId),
    queryFn: ({ signal }) => request<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}`, { signal }),
    enabled: Boolean(projectId),
  });
}

export function assertProjectScope<T extends { project_id?: string | null }>(projectId: string, records: T[], resourceLabel: string): T[] {
  const leaked = records.find((record) => record.project_id !== projectId);
  if (!leaked) return records;
  throw new ApiProblem(500, {
    code: "PROJECT_SCOPE_MISMATCH",
    title: `${resourceLabel}项目范围不一致`,
    detail: `服务返回了属于项目 ${leaked.project_id} 的记录，当前项目为 ${projectId}。为避免跨项目数据混淆，界面已拒绝展示。`,
    repair: "刷新页面；若问题持续，请检查后端项目查询过滤与投影索引。",
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
