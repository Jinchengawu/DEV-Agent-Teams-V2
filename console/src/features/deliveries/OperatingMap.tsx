import type { Delivery } from "../../entities/delivery/model";
import { deliveryStageIndex } from "../../entities/delivery/model";
import { statusLabel } from "../../i18n";

const stages = ["需求规划", "计划审批", "UI 设计", "设计审批", "前后端实现", "测试验证", "发布审批", "应用"];

export function OperatingMap({ delivery }: { delivery?: Delivery }) {
  const current = delivery ? deliveryStageIndex(delivery) : -1;
  return <section className="operating-map" aria-label="交付运行态势">
    <div className="map-label"><span>运行态势</span><b>{delivery ? statusLabel(delivery.status) : "当前无运行"}</b></div>
    <div className="map-track">{stages.map((stage, index) => <div key={stage} className={index < current ? "done" : index === current ? "live" : ""}>
      <i>{String(index + 1).padStart(2, "0")}</i><strong>{stage}</strong><small>{[1, 3, 6].includes(index) ? "人工授权" : "机器控制"}</small>
    </div>)}</div>
  </section>;
}
