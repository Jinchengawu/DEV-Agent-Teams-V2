import { CheckCircle2, CircleAlert, GitCommitHorizontal, ShieldCheck } from "lucide-react";
import { artifactTypeLabel, identityLabel, statusLabel } from "../../i18n";
import type { Delivery, EvidenceRecord, ProductEvent } from "../../entities/delivery/model";
import { ConflictState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import type { DeliveryDecision } from "./api";
import type { PipelineRun } from "./api";

type Props = {
  delivery: Delivery;
  pipelineRun?: PipelineRun;
  events: ProductEvent[];
  evidence: EvidenceRecord[];
  decisionPending: boolean;
  decisionError?: Error | null;
  onDecision: (decision: DeliveryDecision) => void;
};

export function DeliveryDetail({ delivery, pipelineRun, events, evidence, decisionPending, decisionError, onDecision }: Props) {
  const verified = evidence.filter((item) => item.status === "verified");
  const graphNodes = pipelineRun ? graphNodeProjections(pipelineRun.snapshot) : [];
  return <div className="delivery-detail">
    <section className="detail-hero">
      <div><span className="eyebrow">交付 {delivery.id.slice(0, 8)}</span><h2>{delivery.user_request}</h2><p>流水线 {delivery.pipeline_revision_id ?? delivery.journey_revision_id ?? "未绑定发布版本"} · 聚合版本 {delivery.version}</p></div>
      <StatusBadge value={delivery.status}/>
    </section>

    <ConflictState error={decisionError}/>
    {delivery.error_code && <div className="repair-callout"><CircleAlert size={18}/><div><b>交付未能继续：{delivery.error_code}</b><span>请根据失败代码修正需求或运行依赖，再创建新的交付。失败运行不会污染沙箱主分支。</span></div></div>}

    {pipelineRun && <section className="panel pipeline-run-ledger"><div className="panel-head"><span>ACWM DAG 运行账本</span><small>GraphRun V{pipelineRun.version} · {pipelineRun.status}</small></div><div className="pipeline-run-meta"><span>不可变图指纹</span><code>{pipelineRun.graph_fingerprint}</code></div><div className="pipeline-node-projections">{graphNodes.map((node) => <article key={node.node_id}><i data-status={node.status}/><b>{node.node_id}</b><StatusBadge value={node.status}/><small>尝试 {node.attempt}</small></article>)}</div></section>}

    <div className="detail-grid">
      <section className="panel artifact-panel">
        <div className="panel-head"><span>计划与授权</span><small>{identityLabel(delivery.planning_identity)}</small></div>
        {delivery.requirements ? <><h3>{delivery.requirements.summary}</h3><ul>{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code>{item.statement}</li>)}</ul></> : <p className="muted">需求产物尚未生成。</p>}
        {delivery.task && <div className="task-contract"><span>单一任务合同</span><b>{delivery.task.title}</b><small>{delivery.task.acceptance_ids.join(" · ")}</small></div>}
        {delivery.plan_gate && <GateSubject label="计划审批主题" sha={delivery.plan_gate.subject_sha256} revision={delivery.plan_gate.revision}/>} 
        {delivery.status === "awaiting_plan_decision" && <div className="decision-row"><button className="primary" disabled={decisionPending} onClick={() => onDecision("approve-plan")}>批准计划并开始执行</button><button className="danger" disabled={decisionPending} onClick={() => onDecision("reject-plan")}>拒绝计划</button></div>}
      </section>

      <section className="panel artifact-panel">
        <div className="panel-head"><span>候选变更与机器验证</span><small>{identityLabel(delivery.execution_identity ?? undefined)}</small></div>
        {delivery.candidate ? <>
          <div className="revision-pair"><Revision label="基线" value={delivery.candidate.base_revision}/><GitCommitHorizontal size={19}/><Revision label="候选" value={delivery.candidate.candidate_revision}/></div>
          <div className="diff-meta"><span>变更 {delivery.candidate.changed_files.length} 个文件</span><code>{delivery.candidate.diff_sha256}</code></div>
          <pre className="unified-diff">{delivery.candidate.unified_diff}</pre>
        </> : <p className="muted">尚未形成经过验证的 Git Candidate。</p>}
        {delivery.verification && <div className={`verification ${delivery.verification.status === "passed" ? "verified" : "invalid"}`}><ShieldCheck size={18}/><div><b>固定机器测试：{statusLabel(delivery.verification.status)}</b><code>{delivery.verification.commands.join(" && ")}</code><small>退出码 {delivery.verification.exit_code} · 日志哈希 {delivery.verification.log_sha256}</small></div></div>}
        {delivery.candidate_gate && <GateSubject label="候选审批主题" sha={delivery.candidate_gate.subject_sha256} revision={delivery.candidate_gate.revision}/>} 
        {delivery.status === "awaiting_candidate_decision" && <div className="decision-row"><button className="primary" disabled={decisionPending} onClick={() => onDecision("accept-candidate")}>接受候选并原子应用</button><button className="danger" disabled={decisionPending} onClick={() => onDecision("reject-candidate")}>拒绝候选</button></div>}
        {delivery.apply_receipt && <div className="apply-receipt"><CheckCircle2 size={20}/><div><b>应用回执已核验</b><small>应用前 {delivery.apply_receipt.before_revision}<br/>候选 {delivery.apply_receipt.candidate_revision}<br/>应用后 {delivery.apply_receipt.after_revision}</small></div></div>}
      </section>
    </div>

    <div className="detail-grid evidence-rail">
      <section className="panel"><div className="panel-head"><span>可信证据轨</span><small>{verified.length}/{evidence.length} 已验证</small></div>
        {evidence.length === 0 ? <p className="muted">当前阶段尚无证据。只有真实产物生成后才会出现记录。</p> : <div className="evidence-list">{evidence.map((item) => <article key={item.id}><StatusBadge value={item.status}/><div><b>{artifactTypeLabel(item.kind)}</b><small>{item.producer_identity} · {item.source_id}</small></div><code>{item.content_sha256 ?? "无可验证内容哈希"}</code></article>)}</div>}
      </section>
      <section className="panel"><div className="panel-head"><span>产品事件</span><small>仅显示已提交事件</small></div>
        {events.length === 0 ? <p className="muted">尚无已提交事件。</p> : <ol className="event-stream">{events.map((event) => <li key={event.id}><i/><div><b>{event.event_type}</b><small>{event.occurred_at} · v{event.aggregate_version}</small></div></li>)}</ol>}
      </section>
    </div>
  </div>;
}

type GraphNodeProjection = { node_id: string; status: string; attempt: number };

function graphNodeProjections(snapshot: Record<string, unknown>): GraphNodeProjection[] {
  const nodes = snapshot.nodes;
  if (!Array.isArray(nodes)) return [];
  return nodes.flatMap((node) => {
    if (typeof node !== "object" || node === null) return [];
    const value = node as Record<string, unknown>;
    if (typeof value.node_id !== "string" || typeof value.status !== "string") return [];
    return [{ node_id: value.node_id, status: value.status, attempt: typeof value.attempt === "number" ? value.attempt : 0 }];
  });
}

function GateSubject({ label, sha, revision }: { label: string; sha: string; revision: number }) {
  return <div className="gate-subject"><span>{label} · 修订 {revision}</span><code>{sha}</code></div>;
}

function Revision({ label, value }: { label: string; value: string }) {
  return <div><small>{label} Revision</small><code>{value}</code></div>;
}
