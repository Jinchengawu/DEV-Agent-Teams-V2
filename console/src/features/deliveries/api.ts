import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request, type Delivery, type EvidenceRecord, type ProductEvent } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

type Pipeline = components["schemas"]["Pipeline"];
export type PipelineRun = components["schemas"]["PipelineRunRecord"];

export const deliveryKeys = {
  all: (projectId: string) => ["deliveries", projectId] as const,
  detail: (id: string) => ["deliveries", id] as const,
  events: (id: string) => ["deliveries", id, "events"] as const,
  evidence: (id: string) => ["deliveries", id, "evidence"] as const,
};

export function useDeliveries(projectId: string) {
  return useQuery({ queryKey: deliveryKeys.all(projectId), queryFn: () => request<Delivery[]>(`/v1/deliveries?project_id=${encodeURIComponent(projectId)}`), refetchInterval: 1500 });
}

export function useDelivery(id?: string) {
  return useQuery({ queryKey: deliveryKeys.detail(id ?? ""), queryFn: () => request<Delivery>(`/v1/deliveries/${id}`), enabled: Boolean(id), refetchInterval: 1000 });
}

export function useDeliveryEvents(id?: string) {
  return useQuery({ queryKey: deliveryKeys.events(id ?? ""), queryFn: () => request<ProductEvent[]>(`/v1/deliveries/${id}/events`), enabled: Boolean(id), refetchInterval: 1000 });
}

export function useDeliveryEvidence(id?: string) {
  return useQuery({ queryKey: deliveryKeys.evidence(id ?? ""), queryFn: () => request<EvidenceRecord[]>(`/v1/deliveries/${id}/evidence`), enabled: Boolean(id), refetchInterval: 1000 });
}

export function useDeliveryPipelineRun(deliveryId?: string, runId?: string | null) {
  return useQuery({
    queryKey: ["pipeline-runs", runId ?? "", deliveryId ?? ""],
    queryFn: () => request<PipelineRun>(`/v1/deliveries/${deliveryId}/pipeline-run`),
    enabled: Boolean(deliveryId && runId),
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

export type DeliveryDecision = "approve-plan" | "reject-plan" | "accept-candidate" | "reject-candidate";
type DecisionInput = { delivery: Delivery; decision: DeliveryDecision };

export function useDeliveryDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ delivery, decision }: DecisionInput) => {
      const plan = decision.endsWith("plan");
      const gate = plan ? delivery.plan_gate : delivery.candidate_gate;
      const path = plan ? "plan-decision" : "candidate-decision";
      if (!gate) throw new Error("当前审批主题尚未生成，请等待状态推进。");
      return request<Delivery>(`/v1/deliveries/${delivery.id}/${path}`, {
        method: "POST",
        body: JSON.stringify({
          decision: decision === "approve-plan" ? "approve" : decision === "accept-candidate" ? "accept" : "reject",
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
