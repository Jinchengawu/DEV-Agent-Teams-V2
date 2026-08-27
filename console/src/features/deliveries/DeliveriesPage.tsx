import { useEffect, useState } from "react";
import { Button, Collapse, Input, Select } from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { DeliveryDetail } from "./DeliveryDetail";
import { OperatingMap } from "./OperatingMap";
import { useCreateDelivery, useDeliveries, useDelivery, useDeliveryDecision, useDeliveryEvents, useDeliveryEvidence, useDeliveryKnowledgePublications, useDeliveryPipelineRun, useDeliveryPipelines, useRetryKnowledgePublication } from "./api";
import { useProject, useProjectId } from "../../entities/project/api";

export function DeliveriesPage() {
  const projectId = useProjectId();
  const [searchParams, setSearchParams] = useSearchParams();
  const project = useProject(projectId);
  const deliveries = useDeliveries(projectId);
  const [selectedId, setSelectedId] = useState<string>();
  const [requestText, setRequestText] = useState("增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。");
  const pipelines = useDeliveryPipelines();
  const activePipelines = project.data?.pipeline_bindings.filter((binding) => binding.enabled).map((binding) => ({ id: binding.pipeline_id, name: pipelines.data?.find((pipeline) => pipeline.id === binding.pipeline_id)?.name ?? binding.pipeline_id, active_revision: binding.pipeline_revision })) ?? [];
  const [pipelineRevisionId, setPipelineRevisionId] = useState<string>();
  useEffect(() => { if (!pipelineRevisionId && activePipelines[0]?.active_revision) setPipelineRevisionId(`${activePipelines[0].id}:${activePipelines[0].active_revision}`); }, [activePipelines, pipelineRevisionId]);
  useEffect(() => { setSelectedId(undefined); setPipelineRevisionId(undefined); }, [projectId]);
  useEffect(() => {
    const requestedId = searchParams.get("delivery_id");
    if (requestedId && deliveries.data?.some((delivery) => delivery.id === requestedId)) {
      setSelectedId(requestedId);
      return;
    }
    if (!selectedId && deliveries.data?.length) setSelectedId(deliveries.data[0].id);
  }, [deliveries.data, searchParams, selectedId]);
  const selectDelivery = (deliveryId: string) => {
    setSelectedId(deliveryId);
    setSearchParams({ delivery_id: deliveryId }, { replace: true });
  };
  const selected = useDelivery(selectedId, projectId);
  const events = useDeliveryEvents(selectedId, projectId);
  const evidence = useDeliveryEvidence(selectedId, projectId);
  const publications = useDeliveryKnowledgePublications(selectedId, projectId);
  const pipelineRun = useDeliveryPipelineRun(selectedId, selected.data?.pipeline_run_id);
  const create = useCreateDelivery(projectId, selectDelivery);
  const decision = useDeliveryDecision();
  const retryPublication = useRetryKnowledgePublication(selectedId);

  if (deliveries.isLoading) return <LoadingState label="正在载入交付历史…"/>;
  if (deliveries.error) return <ErrorState error={deliveries.error} retry={() => deliveries.refetch()}/>;
  const active = selected.data ?? deliveries.data?.[0];

  return <>
    <OperatingMap delivery={active}/>
    <div className="delivery-workbench">
      <aside className="delivery-sidebar">
        <div className="sidebar-title"><div><span className="eyebrow">项目工作区</span><b>{project.data?.project.name ?? projectId}</b></div><Button type="text" aria-label="刷新交付" icon={<RefreshCw size={15}/>} onClick={() => deliveries.refetch()}/></div>
        <Collapse
          className="create-drawer"
          defaultActiveKey={deliveries.data?.length === 0 ? ["create"] : []}
          items={[{
            key: "create",
            label: <span className="create-drawer__title"><Plus size={15}/>新建交付</span>,
            children: <div className="create-drawer__body">
              <label>执行流水线<Select aria-label="执行流水线" value={pipelineRevisionId} placeholder="请选择已激活流水线" onChange={setPipelineRevisionId} options={activePipelines.map((pipeline) => ({ value: `${pipeline.id}:${pipeline.active_revision}`, label: `${pipeline.name} · R${pipeline.active_revision}` }))}/></label>
              {pipelines.isSuccess && activePipelines.length === 0 && <p className="field-warning">没有已激活流水线。请先到可视化编排中发布并激活一个版本。</p>}
              <Input.TextArea aria-label="交付需求" value={requestText} onChange={(event) => setRequestText(event.target.value)} autoSize={{ minRows: 4, maxRows: 8 }}/>
              <Button type="primary" block loading={create.isPending} disabled={!requestText.trim() || !pipelineRevisionId} onClick={() => create.mutate({ userRequest: requestText, pipelineRevisionId })}>按所选流水线启动闭环</Button>
              {create.error && <ErrorState error={create.error}/>}
            </div>,
          }]}
        />
        <div className="delivery-list">{deliveries.data?.map((item) => <Button type="text" block key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => selectDelivery(item.id)}><span><b>{item.user_request}</b><small>{item.id.slice(0, 8)} · v{item.version}</small></span><StatusBadge value={item.status}/></Button>)}</div>
      </aside>
      <div className="delivery-canvas">
        {selected.isLoading && <LoadingState label="正在读取交付聚合与证据…"/>}
        {selected.error && <ErrorState error={selected.error} retry={() => selected.refetch()}/>}
        {selected.data && <DeliveryDetail
          delivery={selected.data}
          pipelineRun={pipelineRun.data}
          events={events.data ?? []}
          evidence={evidence.data ?? []}
          evidenceError={evidence.error}
          publications={publications.data ?? []}
          publicationsError={publications.error}
          publicationRetryPending={retryPublication.isPending}
          publicationRetryError={retryPublication.error}
          onRetryPublication={(publicationId, expectedVersion) => retryPublication.mutate({ publicationId, expectedVersion })}
          decisionPending={decision.isPending}
          decisionError={decision.error}
          onDecision={(value) => decision.mutate({ delivery: selected.data!, decision: value })}
        />}
        {!selectedId && <div className="state-box state-empty"><b>还没有交付</b><span>从左侧输入一个有边界的后端需求开始。</span></div>}
      </div>
    </div>
  </>;
}
