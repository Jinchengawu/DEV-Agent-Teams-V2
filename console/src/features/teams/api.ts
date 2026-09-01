import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";

export type TeamTemplate = components["schemas"]["TeamTemplate"];
export type TeamTemplateCreate = components["schemas"]["TeamTemplateCreate"];
export type TeamTemplateDraft = components["schemas"]["TeamTemplateDraft"];
export type TeamTemplateDraftPatch = components["schemas"]["TeamTemplateDraftPatch"];
export type TeamTemplateRevision = components["schemas"]["TeamTemplateRevision"];

export const teamKeys = {
  all: ["team-templates"] as const,
  drafts: (templateId: string) => ["team-template-drafts", templateId] as const,
};

export function useTeamTemplates() {
  return useQuery({ queryKey: teamKeys.all, queryFn: ({ signal }) => request<TeamTemplate[]>("/v1/team-templates", { signal }) });
}

export function useTeamTemplateDrafts(templateId?: string) {
  return useQuery({
    queryKey: teamKeys.drafts(templateId ?? ""),
    queryFn: ({ signal }) => request<TeamTemplateDraft[]>(`/v1/team-templates/${encodeURIComponent(templateId!)}/drafts`, { signal }),
    enabled: Boolean(templateId),
  });
}

export function useTeamTemplateRevision(template?: TeamTemplate) {
  return useQuery({
    queryKey: ["team-template-revision", template?.id ?? "", template?.latest_revision ?? 0],
    queryFn: ({ signal }) => request<TeamTemplateRevision>(
      `/v1/team-templates/${encodeURIComponent(template!.id)}/revisions/${template!.latest_revision}`,
      { signal },
    ),
    enabled: Boolean(template?.latest_revision),
  });
}

export function useCreateTeamTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: TeamTemplateCreate) => request<{ template: TeamTemplate; draft: TeamTemplateDraft }>("/v1/team-templates", { method: "POST", body: JSON.stringify(payload) }),
    onSuccess: async ({ template }) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: teamKeys.all }),
        client.invalidateQueries({ queryKey: teamKeys.drafts(template.id) }),
      ]);
    },
  });
}

export function usePatchTeamTemplateDraft(templateId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ draftId, patch }: { draftId: string; patch: TeamTemplateDraftPatch }) => request<TeamTemplateDraft>(`/v1/team-template-drafts/${encodeURIComponent(draftId)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => client.invalidateQueries({ queryKey: teamKeys.drafts(templateId ?? "") }),
  });
}

export function useValidateTeamTemplateDraft(templateId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ draftId, expectedVersion }: { draftId: string; expectedVersion: number }) => request<TeamTemplateDraft>(`/v1/team-template-drafts/${encodeURIComponent(draftId)}/validate`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: teamKeys.drafts(templateId ?? "") }),
  });
}

export function usePublishTeamTemplateDraft(templateId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ draftId, expectedVersion }: { draftId: string; expectedVersion: number }) => request<TeamTemplateRevision>(`/v1/team-template-drafts/${encodeURIComponent(draftId)}/publish`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: teamKeys.all }),
        client.invalidateQueries({ queryKey: teamKeys.drafts(templateId ?? "") }),
      ]);
    },
  });
}
