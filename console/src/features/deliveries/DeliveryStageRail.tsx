import type { Delivery } from "../../entities/delivery/model";
import { deliveryStageIndex } from "../../entities/delivery/model";

const stages = [
  { label: "需求规划", owner: "规划身份" },
  { label: "计划审批", owner: "人工授权" },
  { label: "UI 设计", owner: "设计角色" },
  { label: "设计审批", owner: "人工授权" },
  { label: "前后端实现", owner: "Codex CLI" },
  { label: "测试验证", owner: "验证角色" },
  { label: "发布审批", owner: "人工授权" },
  { label: "原子应用", owner: "机器控制" },
] as const;

export function DeliveryStageRail({ delivery }: { delivery: Delivery }) {
  const current = deliveryStageIndex(delivery);
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
