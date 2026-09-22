import type { Delivery } from "../../entities/delivery/model";
const stages = [
  { label: "Boundary", owner: "交付负责人" },
  { label: "Plan Gate", owner: "Editor / Administrator" },
  { label: "Design Gate", owner: "Editor / Administrator" },
  { label: "Workcells", owner: "产品可观测 Main + Child" },
  { label: "Verification", owner: "固定机器验证" },
  { label: "Candidate", owner: "不可变 Git Candidate" },
  { label: "Evidence", owner: "产品证据账本" },
] as const;

export function DeliveryStageRail({ delivery }: { delivery: Delivery }) {
  const current = interactionStageIndex(delivery);
  const terminalFailure = current < 0;
  return <section className="stage-shell" aria-label="交付阶段">
    <ol className="stage-rail">
      {stages.map((stage, index) => {
        const done = !terminalFailure && index < current;
        const active = !terminalFailure && index === current;
        return <li key={stage.label} className={`${done ? "done" : ""} ${active ? "current" : ""}`} aria-current={active ? "step" : undefined}>
          <b>{done ? "✓" : String(index + 1).padStart(2, "0")}</b>
          <span>{stage.label}<small>{stage.owner}</small></span>
        </li>;
      })}
    </ol>
  </section>;
}

function interactionStageIndex(delivery: Delivery): number {
  if (["failed", "rejected", "cancelled"].includes(delivery.status)) return -1;
  if (delivery.status === "completed") return stages.length;
  if (delivery.status === "awaiting_candidate_decision" || delivery.status === "needs_attention" || delivery.status === "applying") return 6;
  if (delivery.status === "verifying") return 4;
  if (delivery.status === "executing" || delivery.status === "cancelling") return 3;
  if (delivery.status === "awaiting_design_decision") return 2;
  if (delivery.status === "awaiting_plan_decision") return 1;
  return 0;
}
