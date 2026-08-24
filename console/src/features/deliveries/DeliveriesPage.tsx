import { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { DeliveryDetail } from "./DeliveryDetail";
import { OperatingMap } from "./OperatingMap";
import { useCreateDelivery, useDeliveries, useDelivery, useDeliveryDecision, useDeliveryEvents, useDeliveryEvidence, useDeliveryPipelineRun, useDeliveryPipelines } from "./api";
import { useProject, useProjectId } from "../../entities/project/api";

export function DeliveriesPage() {
  const projectId = useProjectId();
  const project = useProject(projectId);
  const deliveries = useDeliveries(projectId);
  const [selectedId, setSelectedId] = useState<string>();
  const [requestText, setRequestText] = useState("增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。");
  const pipelines = useDeliveryPipelines();
  const activePipelines = project.data?.pipeline_bindings.filter((binding) => binding.enabled).map((binding) => ({ id: binding.pipeline_id, name: pipelines.data?.find((pipeline) => pipeline.id === binding.pipeline_id)?.name ?? binding.pipeline_id, active_revision: binding.pipeline_revision })) ?? [];
  const [pipelineRevisionId, setPipelineRevisionId] = useState<string>();
  useEffect(() => { if (!pipelineRevisionId && activePipelines[0]?.active_revision) setPipelineRevisionId(`${activePipelines[0].id}:${activePipelines[0].active_revision}`); }, [activePipelines, pipelineRevisionId]);
  useEffect(() => { setSelectedId(undefined); setPipelineRevisionId(undefined); }, [projectId]);
  useEffect(() => { if (!selectedId && deliveries.data?.length) setSelectedId(deliveries.data[0].id); }, [deliveries.data, selectedId]);
  const selected = useDelivery(selectedId);
  const events = useDeliveryEvents(selectedId);
  const evidence = useDeliveryEvidence(selectedId);
  const pipelineRun = useDeliveryPipelineRun(selectedId, selected.data?.pipeline_run_id);
  const create = useCreateDelivery(projectId, setSelectedId);
  const decision = useDeliveryDecision();

  if (deliveries.isLoading) return <LoadingState label="正在载入交付历史…"/>;
  if (deliveries.error) return <ErrorState error={deliveries.error} retry={() => deliveries.refetch()}/>;
  const active = selected.data ?? deliveries.data?.[0];

  return <>
    <OperatingMap delivery={active}/>
    <div className="delivery-workbench">
      <aside className="delivery-sidebar">
        <div className="sidebar-title"><div><span className="eyebrow">项目工作区</span><b>{project.data?.project.name ?? projectId}</b></div><button aria-label="刷新交付" onClick={() => deliveries.refetch()}><RefreshCw size={15}/></button></div>
        <details className="create-drawer" open={deliveries.data?.length === 0}><summary><Plus size={15}/>新建交付</summary><label>执行流水线<select aria-label="执行流水线" value={pipelineRevisionId ?? ""} onChange={(event) => setPipelineRevisionId(event.target.value)}><option value="">请选择已激活流水线</option>{activePipelines.map((pipeline) => <option key={pipeline.id} value={`${pipeline.id}:${pipeline.active_revision}`}>{pipeline.name} · R{pipeline.active_revision}</option>)}</select></label>{pipelines.isSuccess && activePipelines.length === 0 && <p className="field-warning">没有已激活流水线。请先到可视化编排中发布并激活一个版本。</p>}<textarea aria-label="交付需求" value={requestText} onChange={(event) => setRequestText(event.target.value)}/><button className="primary" disabled={create.isPending || !requestText.trim() || !pipelineRevisionId} onClick={() => create.mutate({ userRequest: requestText, pipelineRevisionId })}>按所选流水线启动闭环</button>{create.error && <ErrorState error={create.error}/>}</details>
        <div className="delivery-list">{deliveries.data?.map((item) => <button key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span><b>{item.user_request}</b><small>{item.id.slice(0, 8)} · v{item.version}</small></span><StatusBadge value={item.status}/></button>)}</div>
      </aside>
      <div className="delivery-canvas">
        {selected.isLoading && <LoadingState label="正在读取交付聚合与证据…"/>}
        {selected.error && <ErrorState error={selected.error} retry={() => selected.refetch()}/>}
        {selected.data && <DeliveryDetail
          delivery={selected.data}
          pipelineRun={pipelineRun.data}
          events={events.data ?? []}
          evidence={evidence.data ?? []}
          decisionPending={decision.isPending}
          decisionError={decision.error}
          onDecision={(value) => decision.mutate({ delivery: selected.data!, decision: value })}
        />}
        {!selectedId && <div className="state-box state-empty"><b>还没有交付</b><span>从左侧输入一个有边界的后端需求开始。</span></div>}
      </div>
    </div>
  </>;
}
