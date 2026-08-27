import type { Delivery } from "../../shared/api/client";

const stages = [
  { label: "需求规划", owner: "Codex 模拟 Hermes" },
  { label: "计划审批", owner: "人工授权" },
  { label: "隔离执行", owner: "Codex CLI" },
  { label: "机器验证", owner: "验证器" },
  { label: "候选审批", owner: "人工授权" },
  { label: "原子应用", owner: "机器控制" },
] as const;

const stageByStatus: Record<Delivery["status"], number> = {
  queued: 0,
  planning: 0,
  awaiting_plan_decision: 1,
  executing: 2,
  verifying: 3,
  awaiting_candidate_decision: 4,
  applying: 5,
  completed: 6,
  rejected: 1,
  failed: 0,
  cancelled: 0,
};

function inferredStage(delivery: Delivery): number {
  if (delivery.status !== "failed" && delivery.status !== "cancelled") return stageByStatus[delivery.status];
  if (delivery.apply_receipt) return 6;
  if (delivery.candidate_gate) return 4;
  if (delivery.verification) return 3;
  if (delivery.candidate) return 2;
  if (delivery.plan_gate) return 1;
  return 0;
}

export function DeliveryStageRail({ delivery }: { delivery: Delivery }) {
  const current = inferredStage(delivery);
  return <section className="stage-shell" aria-label="交付阶段">
    <ol className="stage-rail">
      {stages.map((stage, index) => {
        const done = index < current;
        const active = index === current && current < stages.length;
        return <li key={stage.label} className={`${done ? "done" : ""} ${active ? "current" : ""}`} aria-current={active ? "step" : undefined}>
          <b>{done ? "✓" : String(index + 1).padStart(2, "0")}</b>
          <span>{stage.label}<small>{stage.owner}</small></span>
        </li>;
      })}
    </ol>
  </section>;
}
