export { type Delivery, type EvidenceRecord, type ProductEvent } from "../../shared/api/client";

export const terminalDeliveryStates = new Set(["completed", "failed", "rejected", "cancelled"]);

export const deliveryStageIndex: Record<string, number> = {
  queued: 0,
  planning: 0,
  awaiting_plan_decision: 1,
  executing: 2,
  verifying: 3,
  awaiting_candidate_decision: 4,
  applying: 5,
  completed: 6,
};

