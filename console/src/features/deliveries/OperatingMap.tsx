import type { Delivery } from "../../entities/delivery/model";
import { deliveryStageIndex } from "../../entities/delivery/model";
import { statusLabel } from "../../i18n";

const stages = ["需求", "计划审批", "执行", "验证", "候选审批", "应用"];

export function OperatingMap({ delivery }: { delivery?: Delivery }) {
  const current = delivery ? (deliveryStageIndex[delivery.status] ?? -1) : -1;
  return <section className="operating-map" aria-label="交付运行态势">
    <div className="map-label"><span>运行态势</span><b>{delivery ? statusLabel(delivery.status) : "当前无运行"}</b></div>
    <div className="map-track">{stages.map((stage, index) => <div key={stage} className={index < current ? "done" : index === current ? "live" : ""}>
      <i>{String(index + 1).padStart(2, "0")}</i><strong>{stage}</strong><small>{index === 1 || index === 4 ? "人工授权" : "机器控制"}</small>
    </div>)}</div>
  </section>;
}

