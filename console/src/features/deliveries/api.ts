import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request, type Delivery, type EvidenceRecord, type ProductEvent } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";
import { assertProjectScope } from "../../entities/project/api";

type Pipeline = components["schemas"]["Pipeline"];
export type PipelineRun = components["schemas"]["PipelineRunRecord"];
export type KnowledgePublication = components["schemas"]["KnowledgePublication"];

export const deliveryKeys = {
  all: (projectId: string) => ["deliveries", projectId] as const,
  detail: (id: string) => ["deliveries", id] as const,
  events: (id: string) => ["deliveries", id, "events"] as const,
  evidence: (id: string) => ["deliveries", id, "evidence"] as const,
  publications: (id: string) => ["deliveries", id, "knowledge-publications"] as const,
};

export function useDeliveries(projectId: string) {
  return useQuery({ queryKey: deliveryKeys.all(projectId), queryFn: async ({ signal }) => assertProjectScope(projectId, await request<Delivery[]>(`/v1/deliveries?project_id=${encodeURIComponent(projectId)}`, { signal }), "交付列表"), refetchInterval: 1500 });
}

export function useDelivery(id?: string, projectId?: string) {
  return useQuery({ queryKey: [...deliveryKeys.detail(id ?? ""), projectId ?? ""], queryFn: async ({ signal }) => assertProjectScope(projectId!, [await request<Delivery>(`/v1/deliveries/${id}`, { signal })], "交付详情")[0], enabled: Boolean(id && projectId), refetchInterval: 1000 });
}

export function useDeliveryEvents(id?: string, projectId?: string) {
  return useQuery({ queryKey: [...deliveryKeys.events(id ?? ""), projectId ?? ""], queryFn: async ({ signal }) => assertProjectScope(projectId!, await request<ProductEvent[]>(`/v1/deliveries/${id}/events`, { signal }), "交付事件"), enabled: Boolean(id && projectId), refetchInterval: 1000 });
}

export function useDeliveryEvidence(id?: string, projectId?: string) {
  return useQuery({ queryKey: [...deliveryKeys.evidence(id ?? ""), projectId ?? ""], queryFn: async ({ signal }) => assertProjectScope(projectId!, await request<EvidenceRecord[]>(`/v1/deliveries/${id}/evidence`, { signal }), "交付证据"), enabled: Boolean(id && projectId), refetchInterval: 1000 });
}

export function useDeliveryKnowledgePublications(id?: string, projectId?: string) {
  return useQuery({
    queryKey: [...deliveryKeys.publications(id ?? ""), projectId ?? ""],
    queryFn: async ({ signal }) => assertProjectScope(
      projectId!,
      await request<KnowledgePublication[]>(`/v1/deliveries/${id}/knowledge-publications`, { signal }),
      "知识发布列表",
    ),
    enabled: Boolean(id && projectId),
    refetchInterval: 1000,
  });
}

export function pipelineRunReady(
  runId: string | null | undefined,
  status: Delivery["status"] | undefined,
) {
  return Boolean(runId && status !== "queued" && status !== "preparing_context");
}

export function useDeliveryPipelineRun(
  deliveryId?: string,
  runId?: string | null,
  status?: Delivery["status"],
) {
  return useQuery({
    queryKey: ["pipeline-runs", runId ?? "", deliveryId ?? ""],
    queryFn: () => request<PipelineRun>(`/v1/deliveries/${deliveryId}/pipeline-run`),
    enabled: Boolean(deliveryId && pipelineRunReady(runId, status)),
    refetchInterval: 1000,
  });
}

export function useDeliveryPipelines() {
  return useQuery({ queryKey: ["pipelines", "delivery-selector"], queryFn: () => request<Pipeline[]>("/v1/pipelines") });
}

export function useCreateDelivery(projectId: string, onCreated: (id: string) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ userRequest, pipelineRevisionId }: { userRequest: string; pipelineRevisionId?: string }) => request<Delivery>("/v1/deliveries", { method: "POST", body: JSON.stringify({ project_id: projectId, user_request: userRequest, pipeline_revision_id: pipelineRevisionId }) }),
    onSuccess: async (delivery) => { await client.invalidateQueries({ queryKey: deliveryKeys.all(projectId) }); onCreated(delivery.id); },
  });
}

export type DeliveryDecision = "approve-plan" | "reject-plan" | "approve-design" | "reject-design" | "accept-candidate" | "reject-candidate";
type DecisionInput = { delivery: Delivery; decision: DeliveryDecision };

export function useDeliveryDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ delivery, decision }: DecisionInput) => {
      const plan = decision.endsWith("plan");
      const design = decision.endsWith("design");
      const gate = plan ? delivery.plan_gate : design ? delivery.design_gate : delivery.candidate_gate;
      const path = plan ? "plan-decision" : design ? "design-decision" : "candidate-decision";
      if (!gate) throw new Error("当前审批主题尚未生成，请等待状态推进。");
      return request<Delivery>(`/v1/deliveries/${delivery.id}/${path}`, {
        method: "POST",
        body: JSON.stringify({
          decision: decision === "approve-plan" || decision === "approve-design" ? "approve" : decision === "accept-candidate" ? "accept" : "reject",
          expected_version: delivery.version,
          expected_subject_sha256: gate.subject_sha256,
        }),
      });
    },
    onSuccess: async (delivery) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["deliveries"] }),
        client.invalidateQueries({ queryKey: deliveryKeys.detail(delivery.id) }),
        client.invalidateQueries({ queryKey: deliveryKeys.events(delivery.id) }),
      ]);
    },
  });
}

export function useRetryKnowledgePublication(deliveryId?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ publicationId, expectedVersion }: { publicationId: string; expectedVersion: number }) =>
      request<KnowledgePublication>(`/v1/knowledge/publications/${publicationId}/retry`, {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion }),
      }),
    onSuccess: async (publication) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: deliveryKeys.publications(publication.delivery_id) }),
        client.invalidateQueries({ queryKey: deliveryKeys.detail(publication.delivery_id) }),
        client.invalidateQueries({ queryKey: ["pipeline-runs"] }),
      ]);
    },
    meta: { deliveryId },
  });
}
