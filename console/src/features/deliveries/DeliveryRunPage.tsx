import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { DeliveryDetail } from "./DeliveryDetail";
import {
  useDelivery,
  useDeliveryDecision,
  useDeliveryEvents,
  useDeliveryEvidence,
  useDeliveryKnowledgePublications,
  useDeliveryPipelineRun,
  useRetryKnowledgePublication,
} from "./api";
import { projectPath, useProjectId } from "../../entities/project/api";
import { WorkcellExecutionPanel } from "../workcells/WorkcellExecutionPanel";
import { KnowledgeContextPanel } from "./KnowledgeContextPanel";

export function DeliveryRunPage() {
  const { deliveryId } = useParams<{ deliveryId: string }>();
  const projectId = useProjectId();
  const delivery = useDelivery(deliveryId, projectId);
  const events = useDeliveryEvents(deliveryId, projectId);
  const evidence = useDeliveryEvidence(deliveryId, projectId);
  const publications = useDeliveryKnowledgePublications(deliveryId, projectId);
  const pipelineRun = useDeliveryPipelineRun(
    deliveryId,
    delivery.data?.pipeline_run_id,
    delivery.data?.status,
  );
  const decision = useDeliveryDecision();
  const retryPublication = useRetryKnowledgePublication(deliveryId);

  if (!deliveryId) return <ErrorState error={new Error("路由缺少交付 ID，请从交付工作台重新进入。")}/>;
  if (delivery.isLoading) return <LoadingState label="正在读取交付聚合、运行账本与证据…"/>;
  if (delivery.error) return <ErrorState error={delivery.error} retry={() => delivery.refetch()}/>;
  if (!delivery.data) return <ErrorState error={new Error("交付接口未返回可显示的运行。")}/>;

  return <div className="delivery-run-page">
    <Link className="back-link" to={projectPath(projectId, "deliveries")}><ArrowLeft size={16}/>返回交付工作台</Link>
    <DeliveryDetail
      delivery={delivery.data}
      pipelineRun={pipelineRun.data}
      pipelineError={pipelineRun.error}
      events={events.data ?? []}
      eventsError={events.error}
      evidence={evidence.data ?? []}
      evidenceError={evidence.error}
      publications={publications.data ?? []}
      publicationsError={publications.error}
      publicationRetryPending={retryPublication.isPending}
      publicationRetryError={retryPublication.error}
      onRetryPublication={(publicationId, expectedVersion) => retryPublication.mutate({ publicationId, expectedVersion })}
      decisionPending={decision.isPending}
      decisionError={decision.error}
      onDecision={(value) => decision.mutate({ delivery: delivery.data!, decision: value })}
    />
    <KnowledgeContextPanel projectId={projectId} deliveryId={delivery.data.id}/>
    {delivery.data.delivery_execution_snapshot && (
      <WorkcellExecutionPanel deliveryId={delivery.data.id} projectId={projectId}/>
    )}
  </div>;
}
