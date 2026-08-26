import type { Delivery, EvidenceRecord, ProductEvent } from "../../shared/api/client";

export type { Delivery, EvidenceRecord, ProductEvent };

export const terminalDeliveryStates = new Set(["completed", "failed", "rejected", "cancelled"]);

export function deliveryStageIndex(delivery: Delivery): number {
  const fullstack = delivery.pipeline_revision_id?.startsWith("fullstack-product-delivery:")
    || (delivery.project_execution_snapshot?.repositories.length ?? 0) > 1;
  if (["queued", "planning"].includes(delivery.status)) return 0;
  if (delivery.status === "awaiting_plan_decision") return 1;
  if (delivery.status === "awaiting_design_decision") return 3;
  if (delivery.status === "executing") {
    if (fullstack && !delivery.design_gate?.decision) return 2;
    return 4;
  }
  if (delivery.status === "verifying") return fullstack ? 5 : 4;
  if (delivery.status === "awaiting_candidate_decision") return 6;
  if (["applying", "completed"].includes(delivery.status)) return 7;
  return -1;
}
