import { ArrowLeft } from "lucide-react";
import { Collapse } from "antd";
import { Link, useParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { DeliveryDetail } from "./DeliveryDetail";
import {
  useDelivery,
  useCancelEvaluation,
  useDeliveryDecision,
  useDeliveryEvents,
  useDeliveryEvidence,
  useDeliveryKnowledgePublications,
  useDeliveryPipelineRun,
  useRetryKnowledgePublication,
} from "./api";
import { projectPath, useProject, useProjectId } from "../../entities/project/api";
import { useCompleteProjectOnboarding } from "../projects/api";
import { useIdentity } from "../identity/AuthGate";
import { WorkcellExecutionPanel } from "../workcells/WorkcellExecutionPanel";
import { KnowledgeContextPanel } from "./KnowledgeContextPanel";
import { EvaluationExit } from "./EvaluationExit";

export function DeliveryRunPage() {
  const { deliveryId } = useParams<{ deliveryId: string }>();
  const projectId = useProjectId();
  const delivery = useDelivery(deliveryId, projectId);
  const project = useProject(projectId);
  const { user } = useIdentity();
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
  const completeOnboarding = useCompleteProjectOnboarding(projectId);
  const cancelEvaluation = useCancelEvaluation();

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
      canDecidePlan={user.role !== "viewer"}
      canApplyCandidate={user.role === "administrator"}
      canRetryPublication={user.role !== "viewer"}
      onboarding={project.data?.onboarding}
      canCompleteOnboarding={user.role === "administrator"}
      onboardingPending={completeOnboarding.isPending}
      onboardingError={completeOnboarding.error}
      onCompleteOnboarding={() => {
        const gate = delivery.data?.candidate_gate;
        const onboarding = project.data?.onboarding;
        if (!delivery.data || !gate || !onboarding) return;
        completeOnboarding.mutate({ expected_version: onboarding.version, evaluation_delivery_id: delivery.data.id, expected_candidate_gate_subject_sha256: gate.subject_sha256 });
      }}
    />
    <EvaluationExit projectId={projectId} purpose={delivery.data.purpose} status={delivery.data.status}
      ready={project.data?.onboarding.status === "ready"}
      boundEvaluation={project.data?.onboarding.evaluation_delivery_id === delivery.data.id}
      canCancel={user.role !== "viewer"} pending={cancelEvaluation.isPending} error={cancelEvaluation.error}
      onCancel={() => cancelEvaluation.mutate(delivery.data!)}/>
    <section id="delivery-knowledge" className="delivery-secondary-details" aria-label="知识上下文详情">
      <Collapse destroyOnHidden items={[{
        key: "knowledge-context",
        label: "按需查看：冻结知识上下文与 Citation",
        children: <KnowledgeContextPanel projectId={projectId} deliveryId={delivery.data.id}/>,
      }]}/>
    </section>
    {delivery.data.delivery_execution_snapshot && <section id="delivery-workcells" className="delivery-secondary-details" aria-label="Workcell 运行详情">
      <Collapse destroyOnHidden items={[{
        key: "workcell-execution",
        label: "按需查看：Workcell、AgentAttempt 与 Release 运行明细",
        children: <WorkcellExecutionPanel deliveryId={delivery.data.id} projectId={projectId} canOperate={user.role !== "viewer"}/>,
      }]}/>
    </section>}
  </div>;
}
