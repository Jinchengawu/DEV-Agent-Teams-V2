import type { Delivery } from "../../entities/delivery/model";
import { deliveryStageIndex } from "../../entities/delivery/model";

const legacyStages = [
  { label: "需求规划", owner: "规划身份" },
  { label: "计划审批", owner: "人工授权" },
  { label: "UI 设计", owner: "设计角色" },
  { label: "设计审批", owner: "人工授权" },
  { label: "前后端实现", owner: "Codex CLI" },
  { label: "测试验证", owner: "验证角色" },
  { label: "发布审批", owner: "人工授权" },
  { label: "原子应用", owner: "机器控制" },
] as const;

const workcellStages = [
  { label: "需求与任务", owner: "Hermes PM / Admin" },
  { label: "计划审批", owner: "人工授权" },
  { label: "Design Workcell", owner: "Main + Child" },
  { label: "设计审批", owner: "人工授权" },
  { label: "QA Preparation", owner: "Artifact-only" },
  { label: "Frontend / Backend", owner: "并行 Workcell" },
  { label: "QA Delivery", owner: "Main + Child" },
  { label: "Release Gate", owner: "Bundle Hash" },
  { label: "Forward-only Apply", owner: "逐仓远端回读" },
] as const;

export function DeliveryStageRail({ delivery }: { delivery: Delivery }) {
  const isWorkcellDelivery = Boolean(delivery.delivery_execution_snapshot);
  const stages = isWorkcellDelivery ? workcellStages : legacyStages;
  const current = isWorkcellDelivery
    ? workcellDeliveryStageIndex(delivery)
    : deliveryStageIndex(delivery);
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

function workcellDeliveryStageIndex(delivery: Delivery): number {
  if (["failed", "rejected", "cancelled"].includes(delivery.status)) return -1;
  if (delivery.status === "completed") return workcellStages.length;
  if (delivery.status === "needs_attention" || delivery.status === "applying") return 8;
  if (delivery.status === "awaiting_candidate_decision") return 7;
  if (delivery.status === "awaiting_design_decision") return 3;
  if (delivery.status === "awaiting_plan_decision") return 1;
  const candidates = delivery.workcell_candidates ?? {};
  if (candidates.qa) return 7;
  if (candidates.frontend || candidates.backend) return 6;
  if (candidates.design) return 4;
  if (delivery.status === "executing" || delivery.status === "verifying") return 2;
  return 0;
}
