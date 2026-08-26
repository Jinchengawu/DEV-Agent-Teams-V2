import { useState } from "react";
import { Button, Tabs } from "antd";
import { CheckCircle2, CircleAlert, GitCommitHorizontal, PackageCheck, Palette, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { artifactTypeLabel, identityLabel, repositoryRoleLabel, statusLabel } from "../../i18n";
import { projectPath } from "../../entities/project/api";
import type { Delivery, EvidenceRecord, ProductEvent } from "../../entities/delivery/model";
import { ConflictState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import type { DeliveryDecision, PipelineRun } from "./api";

type Props = {
  delivery: Delivery;
  pipelineRun?: PipelineRun;
  events: ProductEvent[];
  evidence: EvidenceRecord[];
  decisionPending: boolean;
  decisionError?: Error | null;
  onDecision: (decision: DeliveryDecision) => void;
};

type RepositoryCandidate = Delivery["repository_candidates"][number];

export function DeliveryDetail({ delivery, pipelineRun, events, evidence, decisionPending, decisionError, onDecision }: Props) {
  const [pendingDecision, setPendingDecision] = useState<DeliveryDecision>();
  const verified = evidence.filter((item) => item.status === "verified");
  const graphNodes = pipelineRun ? graphNodeProjections(pipelineRun.snapshot) : [];
  const repositoryCandidates = delivery.repository_candidates ?? [];
  const designCandidate = repositoryCandidates.find((item) => item.role === "design");
  const confirmation = decisionConfirmation(pendingDecision, Boolean(delivery.release_bundle));

  return <div className="delivery-detail">
    <section className="detail-hero">
      <div><span className="eyebrow">交付 {delivery.id.slice(0, 8)}</span><h2>{delivery.user_request}</h2><p>流水线 {delivery.pipeline_revision_id ?? delivery.journey_revision_id ?? "未绑定发布版本"} · 聚合版本 {delivery.version}</p></div>
      <StatusBadge value={delivery.status}/>
    </section>

    <ConflictState error={decisionError}/>
    {delivery.error_code && <div className="repair-callout"><CircleAlert size={18}/><div><b>交付未能继续：{delivery.error_code}</b><span>请根据失败代码修正需求或运行依赖，再创建新的交付。失败运行不会污染任何项目仓库的 Main。</span></div></div>}

    {pipelineRun && <section className="panel pipeline-run-ledger"><div className="panel-head"><span>ACWM DAG 运行账本</span><small>GraphRun V{pipelineRun.version} · {pipelineRun.status}</small></div><div className="pipeline-run-meta"><span>不可变图指纹</span><code>{pipelineRun.graph_fingerprint}</code></div><div className="pipeline-node-projections">{graphNodes.map((node) => <article key={node.node_id}><i data-status={node.status}/><b>{node.node_id}</b><StatusBadge value={node.status}/><small>尝试 {node.attempt}</small></article>)}</div></section>}

    <div className="detail-grid">
      <section className="panel artifact-panel">
        <div className="panel-head"><span>产品规划与任务授权</span><small>{identityLabel(delivery.planning_identity)}</small></div>
        {delivery.requirements ? <><h3>{delivery.requirements.summary}</h3><ul>{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code>{item.statement}</li>)}</ul></> : <p className="muted">需求产物尚未生成。</p>}
        {delivery.task && <div className="task-contract"><span>单一任务合同</span><b>{delivery.task.title}</b><small>{delivery.task.acceptance_ids.join(" · ")}</small></div>}
        {delivery.plan_gate && <GateSubject label="计划审批主题" sha={delivery.plan_gate.subject_sha256} revision={delivery.plan_gate.revision}/>} 
        {delivery.status === "awaiting_plan_decision" && <div className="decision-row"><Button type="primary" disabled={decisionPending} onClick={() => setPendingDecision("approve-plan")}>批准计划并开始设计</Button><Button danger disabled={decisionPending} onClick={() => setPendingDecision("reject-plan")}>拒绝计划</Button></div>}
      </section>

      <section className="panel artifact-panel design-review-panel">
        <div className="panel-head"><span>UI 设计审查</span><small><Palette size={14}/> Codex 设计角色</small></div>
        {designCandidate ? <CandidateArtifact item={designCandidate}/> : <p className="muted">五角色流水线会先生成独立设计候选；后端流水线不需要此审批。</p>}
        {delivery.design_gate && <GateSubject label="设计审批主题" sha={delivery.design_gate.subject_sha256} revision={delivery.design_gate.revision}/>}
        {delivery.status === "awaiting_design_decision" && <div className="decision-row"><Button type="primary" disabled={decisionPending} onClick={() => setPendingDecision("approve-design")}>批准设计并开始前后端实现</Button><Button danger disabled={decisionPending} onClick={() => setPendingDecision("reject-design")}>拒绝设计</Button></div>}
      </section>
    </div>

    <section className="panel artifact-panel repository-candidates-panel">
      <div className="panel-head"><span>多仓候选变更与机器验证</span><small>{identityLabel(delivery.execution_identity ?? undefined)}</small></div>
      {repositoryCandidates.length ? <Tabs className="repository-candidate-tabs" items={repositoryCandidates.map((item) => ({
        key: item.role,
        label: <span className="repository-tab-label">{repositoryRoleLabel(item.role)}<StatusBadge value={item.verification.status}/></span>,
        children: <CandidateArtifact item={item}/>,
      }))}/> : delivery.candidate ? <LegacyCandidate delivery={delivery}/> : <p className="muted">尚未形成经过验证的 Git Candidate。</p>}

      {delivery.release_bundle && <div className="release-bundle-proof"><PackageCheck size={22}/><div><b>四仓 Release Bundle 已通过系统校验</b><span>{delivery.release_bundle.candidates.length} 个不可变 Candidate · 仓库集合、基线、路径、Diff 和机器测试均已核验</span><code>{delivery.release_bundle.bundle_sha256}</code></div></div>}
      {delivery.candidate_gate && <GateSubject label={delivery.release_bundle ? "发布审批主题" : "候选审批主题"} sha={delivery.candidate_gate.subject_sha256} revision={delivery.candidate_gate.revision}/>}
      {delivery.status === "awaiting_candidate_decision" && <div className="decision-row"><Button type="primary" danger disabled={decisionPending} onClick={() => setPendingDecision("accept-candidate")}>{delivery.release_bundle ? "批准四仓发布并执行 CAS" : "接受候选并原子应用"}</Button><Button danger disabled={decisionPending} onClick={() => setPendingDecision("reject-candidate")}>{delivery.release_bundle ? "拒绝发布包" : "拒绝候选"}</Button></div>}
      {delivery.apply_receipt && <div className="apply-receipt"><CheckCircle2 size={20}/><div><b>单仓应用回执已核验</b><small>应用前 {delivery.apply_receipt.before_revision}<br/>候选 {delivery.apply_receipt.candidate_revision}<br/>应用后 {delivery.apply_receipt.after_revision}</small></div></div>}
      {delivery.release_manifest && <ReleaseManifest delivery={delivery}/>}
    </section>

    <div className="detail-grid evidence-rail">
      <section className="panel"><div className="panel-head"><span>可信证据轨</span><small>{verified.length}/{evidence.length} 已验证</small></div>
        {evidence.length === 0 ? <p className="muted">当前阶段尚无证据。只有真实产物生成后才会出现记录。</p> : <div className="evidence-list">{evidence.map((item) => <article key={item.id}><StatusBadge value={item.status}/><div><b>{artifactTypeLabel(item.kind)}</b><small>{item.producer_identity} · {item.source_id}</small></div><code>{item.content_sha256 ?? "无可验证内容哈希"}</code></article>)}</div>}
        <Link className="secondary evidence-ledger-link" to={`${projectPath(delivery.project_id, "evidence")}?delivery_id=${encodeURIComponent(delivery.id)}`}>在项目证据账本中查看</Link>
      </section>
      <section className="panel"><div className="panel-head"><span>产品事件</span><small>仅显示已提交事件</small></div>
        {events.length === 0 ? <p className="muted">尚无已提交事件。</p> : <ol className="event-stream">{events.map((event) => <li key={event.id}><i/><div><b>{event.event_type}</b><small>{event.occurred_at} · v{event.aggregate_version}</small></div></li>)}</ol>}
      </section>
    </div>
    <ConfirmDialog open={Boolean(pendingDecision)} title={confirmation.title} detail={confirmation.detail} confirmLabel={confirmation.label} tone={pendingDecision?.startsWith("reject") || pendingDecision === "accept-candidate" ? "danger" : "warning"} pending={decisionPending} onCancel={() => setPendingDecision(undefined)} onConfirm={() => { if (pendingDecision) onDecision(pendingDecision); setPendingDecision(undefined); }}/>
  </div>;
}

function CandidateArtifact({ item }: { item: RepositoryCandidate }) {
  return <div className="repository-candidate">
    <div className="candidate-heading"><div><span className="eyebrow">{repositoryRoleLabel(item.role)}仓库</span><b>{item.repository_ref}</b></div><StatusBadge value={item.verification.status}/></div>
    <div className="revision-pair"><Revision label="基线" value={item.candidate.base_revision}/><GitCommitHorizontal size={19}/><Revision label="候选" value={item.candidate.candidate_revision}/></div>
    <div className="diff-meta"><span>变更 {item.candidate.changed_files.length} 个文件 · {item.producer_identity}</span><code>{item.candidate.diff_sha256}</code></div>
    <pre className="unified-diff">{item.candidate.unified_diff}</pre>
    <Verification status={item.verification.status} commands={item.verification.commands} exitCode={item.verification.exit_code} logSha={item.verification.log_sha256}/>
  </div>;
}

function LegacyCandidate({ delivery }: { delivery: Delivery }) {
  if (!delivery.candidate) return null;
  return <div className="repository-candidate">
    <div className="revision-pair"><Revision label="基线" value={delivery.candidate.base_revision}/><GitCommitHorizontal size={19}/><Revision label="候选" value={delivery.candidate.candidate_revision}/></div>
    <div className="diff-meta"><span>变更 {delivery.candidate.changed_files.length} 个文件</span><code>{delivery.candidate.diff_sha256}</code></div>
    <pre className="unified-diff">{delivery.candidate.unified_diff}</pre>
    {delivery.verification && <Verification status={delivery.verification.status} commands={delivery.verification.commands} exitCode={delivery.verification.exit_code} logSha={delivery.verification.log_sha256}/>}
  </div>;
}

function Verification({ status, commands, exitCode, logSha }: { status: string; commands: string[]; exitCode: number; logSha: string }) {
  return <div className={`verification ${status === "passed" ? "verified" : "invalid"}`}><ShieldCheck size={18}/><div><b>固定机器测试：{statusLabel(status)}</b><code>{commands.join(" && ")}</code><small>退出码 {exitCode} · 日志哈希 {logSha}</small></div></div>;
}

function ReleaseManifest({ delivery }: { delivery: Delivery }) {
  const manifest = delivery.release_manifest;
  if (!manifest) return null;
  return <div className="release-manifest"><div className="release-manifest-head"><CheckCircle2 size={22}/><div><b>Release Manifest 已激活</b><span>只有四个仓库 Main 都精确等于已展示 Candidate 后，交付才会显示完成。</span><code>{manifest.manifest_sha256}</code></div></div><div className="release-receipts">{manifest.repositories.map((item) => <article key={item.role}><b>{repositoryRoleLabel(item.role)}</b><StatusBadge value={item.receipt.recovered ? "recovered" : "applied"}/><small>应用前 <code>{item.receipt.before_revision}</code></small><small>应用后 <code>{item.receipt.after_revision}</code></small></article>)}</div></div>;
}

function decisionConfirmation(decision: DeliveryDecision | undefined, releaseBundle: boolean) {
  if (decision === "approve-plan") return { title: "批准计划并启动 UI 设计", detail: "系统将按当前不可变 Gate Subject 启动设计角色；后续仍需独立设计审批、机器验证和发布审批。", label: "确认批准计划" };
  if (decision === "reject-plan") return { title: "拒绝当前交付计划", detail: "该交付将进入拒绝终态，所有项目仓库 Main 均不会发生变化。如需调整需求，必须创建新的交付。", label: "确认拒绝计划" };
  if (decision === "approve-design") return { title: "批准 UI 设计候选", detail: "系统将冻结当前设计 Gate Subject，并并行启动前端与后端实现；测试角色会在两者完成后执行独立验收。", label: "确认批准设计" };
  if (decision === "reject-design") return { title: "拒绝 UI 设计候选", detail: "交付进入拒绝终态，设计候选和证据保留审计，前端、后端和测试仓库不会启动修改。", label: "确认拒绝设计" };
  if (decision === "accept-candidate") return releaseBundle
    ? { title: "批准四仓 Release Bundle", detail: "系统会逐仓执行基于已展示 Base 的 CAS；任何仓库冲突都会停止发布并安全补偿，只有完整 Release Manifest 激活才算完成。", label: "确认发布四个仓库" }
    : { title: "接受候选并原子应用", detail: "系统将使用基线 Revision 执行 CAS 更新。只有 Main 仍等于已展示基线时才会指向当前 Candidate；该操作会改变项目 Main。", label: "确认应用 Candidate" };
  if (decision === "reject-candidate") return { title: releaseBundle ? "拒绝四仓 Release Bundle" : "拒绝当前候选变更", detail: "候选提交与证据会保留用于审计，但所有项目 Main 保持不变。该交付将进入拒绝终态。", label: releaseBundle ? "确认拒绝发布" : "确认拒绝候选" };
  return { title: "确认交付命令", detail: "请检查当前交付状态和证据后继续。", label: "确认执行" };
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
