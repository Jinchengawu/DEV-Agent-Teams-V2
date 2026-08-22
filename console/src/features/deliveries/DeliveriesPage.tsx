import { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { DeliveryDetail } from "./DeliveryDetail";
import { OperatingMap } from "./OperatingMap";
import { useCreateDelivery, useDeliveries, useDelivery, useDeliveryDecision, useDeliveryEvents, useDeliveryEvidence } from "./api";

export function DeliveriesPage() {
  const deliveries = useDeliveries();
  const [selectedId, setSelectedId] = useState<string>();
  const [requestText, setRequestText] = useState("增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。");
  useEffect(() => { if (!selectedId && deliveries.data?.length) setSelectedId(deliveries.data[0].id); }, [deliveries.data, selectedId]);
  const selected = useDelivery(selectedId);
  const events = useDeliveryEvents(selectedId);
  const evidence = useDeliveryEvidence(selectedId);
  const create = useCreateDelivery(setSelectedId);
  const decision = useDeliveryDecision();

  if (deliveries.isLoading) return <LoadingState label="正在载入交付历史…"/>;
  if (deliveries.error) return <ErrorState error={deliveries.error} retry={() => deliveries.refetch()}/>;
  const active = selected.data ?? deliveries.data?.[0];

  return <>
    <OperatingMap delivery={active}/>
    <div className="delivery-workbench">
      <aside className="delivery-sidebar">
        <div className="sidebar-title"><div><span className="eyebrow">工作区</span><b>内置后端沙箱</b></div><button aria-label="刷新交付" onClick={() => deliveries.refetch()}><RefreshCw size={15}/></button></div>
        <details className="create-drawer" open={deliveries.data?.length === 0}><summary><Plus size={15}/>新建交付</summary><textarea aria-label="交付需求" value={requestText} onChange={(event) => setRequestText(event.target.value)}/><button className="primary" disabled={create.isPending || !requestText.trim()} onClick={() => create.mutate(requestText)}>启动真实闭环</button>{create.error && <ErrorState error={create.error}/>}</details>
        <div className="delivery-list">{deliveries.data?.map((item) => <button key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span><b>{item.user_request}</b><small>{item.id.slice(0, 8)} · v{item.version}</small></span><StatusBadge value={item.status}/></button>)}</div>
      </aside>
      <div className="delivery-canvas">
        {selected.isLoading && <LoadingState label="正在读取交付聚合与证据…"/>}
        {selected.error && <ErrorState error={selected.error} retry={() => selected.refetch()}/>}
        {selected.data && <DeliveryDetail delivery={selected.data} events={events.data ?? []} evidence={evidence.data ?? []} decisionPending={decision.isPending} decisionError={decision.error} onDecision={(value) => decision.mutate({ delivery: selected.data!, decision: value })}/>} 
        {!selectedId && <div className="state-box state-empty"><b>还没有交付</b><span>从左侧输入一个有边界的后端需求开始。</span></div>}
      </div>
    </div>
  </>;
}

