import { useQuery } from "@tanstack/react-query";
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
  return useQuery({ queryKey: projectKeys.all, queryFn: ({ signal }) => request<Project[]>("/v1/projects", { signal }) });
}

export function useProject(projectId: string) {
  return useQuery({ queryKey: projectKeys.detail(projectId), queryFn: ({ signal }) => request<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}`, { signal }), enabled: Boolean(projectId) });
}
