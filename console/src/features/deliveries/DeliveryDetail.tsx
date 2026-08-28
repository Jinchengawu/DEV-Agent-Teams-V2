import { useState } from "react";
import { Button, Tabs } from "antd";
import { CheckCircle2, CircleAlert, GitCommitHorizontal, PackageCheck, Palette, RefreshCw, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { artifactTypeLabel, identityLabel, repositoryRoleLabel, statusLabel } from "../../i18n";
import { projectPath } from "../../entities/project/api";
import type { Delivery, EvidenceRecord, ProductEvent } from "../../entities/delivery/model";
import { ConflictState, ErrorState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { Inspector, type InspectorTab } from "../../shared/ui/Inspector";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { DeliveryStageRail } from "./DeliveryStageRail";
import type { DeliveryDecision, PipelineRun } from "./api";

type Props = {
  delivery: Delivery;
  pipelineRun?: PipelineRun;
  pipelineError?: Error | null;
  events: ProductEvent[];
  eventsError?: Error | null;
  evidence: EvidenceRecord[];
  evidenceError?: Error | null;
  publications?: PublicationView[];
  publicationsError?: Error | null;
  publicationRetryPending?: boolean;
  publicationRetryError?: Error | null;
  onRetryPublication?: (publicationId: string, expectedVersion: number) => void;
  decisionPending: boolean;
  decisionError?: Error | null;
  onDecision: (decision: DeliveryDecision) => void;
};

type RepositoryCandidate = Delivery["repository_candidates"][number];
type InspectorSelection = { kind: "plan" } | { kind: "evidence"; record: EvidenceRecord };

type PublicationView = {
  id: string;
  artifact_key: string;
  contract_id: string;
  status: "pending" | "publishing" | "published" | "failed";
  attempt_count: number;
  error_code?: string | null;
  version: number;
};

export function DeliveryDetail({
  delivery,
  pipelineRun,
  pipelineError,
  events,
  eventsError,
  evidence,
  evidenceError,
  publications = [],
  publicationsError,
  publicationRetryPending = false,
  publicationRetryError,
  onRetryPublication,
  decisionPending,
  decisionError,
  onDecision,
}: Props) {
  const [pendingDecision, setPendingDecision] = useState<DeliveryDecision>();
  const [inspectorSelection, setInspectorSelection] = useState<InspectorSelection>();
  const verified = evidence.filter((item) => item.status === "verified");
  const graphNodes = pipelineRun ? graphNodeProjections(pipelineRun.snapshot) : [];
  const repositoryCandidates = delivery.repository_candidates ?? [];
  const designCandidate = repositoryCandidates.find((item) => item.role === "design");
  const confirmation = decisionConfirmation(pendingDecision, Boolean(delivery.release_bundle));
  const blockingPublications = publications.filter((publication) => publication.status !== "published");
  const inspector = inspectorModel(inspectorSelection, delivery);

  return <div className="delivery-detail">
    <section className="run-hero surface-card">
      <div><span className="eyebrow">交付 {delivery.id.slice(0, 8)} · 真实运行</span><h2>{delivery.user_request}</h2><p className="run-meta">流水线 {delivery.pipeline_revision_id ?? delivery.journey_revision_id ?? "未绑定发布版本"} · 聚合版本 {delivery.version} · 更新于 {formatDateTime(delivery.updated_at)}</p></div>
      <StatusBadge value={delivery.status}/>
    </section>

    <DeliveryStageRail delivery={delivery}/>
    <ConflictState error={decisionError}/>
    {delivery.error_code && <div className="repair-callout"><CircleAlert size={18}/><div><b>交付未能继续：{delivery.error_code}</b><span>请根据失败代码修正需求或运行依赖，再创建新的交付。失败运行不会污染任何项目仓库的 Main。</span></div></div>}
    {evidenceError && <ErrorState error={evidenceError}/>}
    {publicationsError && <ErrorState error={publicationsError}/>}
    {publicationRetryError && <ErrorState error={publicationRetryError}/>}
    {blockingPublications.length > 0 && <section className="knowledge-publication-blocker surface-card">
      <div><CircleAlert size={19}/><div><b>知识发布阻塞</b><p>AgentRun 与 Stage 已成功；系统保持 Delivery 非终态和项目租约，不会重跑 Agent，也不会提前打开下一 Gate。</p></div></div>
      <div className="knowledge-publication-list">{blockingPublications.map((publication) => <article key={publication.id}>
        <span><b>{publication.contract_id}</b><small>Artifact {publication.artifact_key} · 尝试 {publication.attempt_count} · {publication.error_code ?? publication.status}</small></span>
        <StatusBadge value={publication.status}/>
        {publication.status === "failed" && <Button className="button-icon" disabled={publicationRetryPending || !onRetryPublication} onClick={() => onRetryPublication?.(publication.id, publication.version)}><RefreshCw size={14}/>{publicationRetryPending ? "正在重试发布…" : "只重试发布"}</Button>}
      </article>)}</div>
    </section>}

    {pipelineError && <ErrorState error={pipelineError}/>}
    {pipelineRun && <section className="panel surface-card pipeline-run-ledger"><div className="panel-head"><span>ACWM DAG 运行账本</span><small>GraphRun V{pipelineRun.version} · {pipelineRun.status}</small></div><div className="pipeline-run-meta"><span>不可变图指纹</span><code>{pipelineRun.graph_fingerprint}</code></div><div className="pipeline-node-projections">{graphNodes.map((node) => <article key={node.node_id}><i data-status={node.status}/><b>{node.node_id}</b><StatusBadge value={node.status}/><small>尝试 {node.attempt}</small></article>)}</div></section>}

    <div className="detail-grid">
      <section className="panel surface-card artifact-panel">
        <div className="panel-head"><div><span>产品规划与任务授权</span><small>{identityLabel(delivery.planning_identity)}</small></div>{delivery.plan_gate && <Button className={delivery.status === "awaiting_plan_decision" ? "primary screen-primary" : "secondary"} onClick={() => setInspectorSelection({ kind: "plan" })}>{delivery.status === "awaiting_plan_decision" ? "审查计划" : "检查计划"}</Button>}</div>
        {delivery.requirements ? <><h3>{delivery.requirements.summary}</h3><ul>{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code>{item.statement}</li>)}</ul></> : <p className="muted">需求产物尚未生成。</p>}
        {delivery.task && <div className="task-contract"><span>单一任务合同</span><b>{delivery.task.title}</b><small>{delivery.task.acceptance_ids.join(" · ")}</small></div>}
        {delivery.plan_gate && <GateSubject label="计划审批主题" sha={delivery.plan_gate.subject_sha256} revision={delivery.plan_gate.revision}/>} 
        {delivery.status === "awaiting_plan_decision" && <div className="decision-row"><Button type="primary" disabled={decisionPending} onClick={() => setPendingDecision("approve-plan")}>批准计划并开始设计</Button><Button danger disabled={decisionPending} onClick={() => setPendingDecision("reject-plan")}>拒绝计划</Button></div>}
      </section>

      <section className="panel surface-card artifact-panel design-review-panel">
        <div className="panel-head"><span>UI 设计审查</span><small><Palette size={14}/> Codex 设计角色</small></div>
        {designCandidate ? <CandidateArtifact item={designCandidate}/> : <p className="muted">五角色流水线会先生成独立设计候选；后端流水线不需要此审批。</p>}
        {delivery.design_gate && <GateSubject label="设计审批主题" sha={delivery.design_gate.subject_sha256} revision={delivery.design_gate.revision}/>}
        {delivery.status === "awaiting_design_decision" && <div className="decision-row"><Button type="primary" disabled={decisionPending} onClick={() => setPendingDecision("approve-design")}>批准设计并开始前后端实现</Button><Button danger disabled={decisionPending} onClick={() => setPendingDecision("reject-design")}>拒绝设计</Button></div>}
      </section>
    </div>

    <section className="panel surface-card artifact-panel repository-candidates-panel">
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
      <section className="panel surface-card"><div className="panel-head"><span>可信证据轨</span><small>{verified.length}/{evidence.length} 已验证</small></div>
        {evidence.length === 0 ? <p className="muted">当前阶段尚无证据。只有真实产物生成后才会出现记录。</p> : <div className="evidence-list">{evidence.map((item) => <Button type="text" key={item.id} onClick={() => setInspectorSelection({ kind: "evidence", record: item })}><StatusBadge value={item.status}/><span><b>{artifactTypeLabel(item.kind)}</b><small>{item.producer_identity} · {item.source_id}</small></span><code>{item.content_sha256 ?? "无可验证内容哈希"}</code></Button>)}</div>}
        <Link className="secondary evidence-ledger-link" to={`${projectPath(delivery.project_id, "evidence")}?delivery_id=${encodeURIComponent(delivery.id)}`}>在项目证据账本中查看</Link>
      </section>
      <section className="panel surface-card"><div className="panel-head"><span>产品事件</span><small>仅显示已提交事件</small></div>
        {eventsError && <ErrorState error={eventsError}/>}
        {events.length === 0 && !eventsError ? <p className="muted">尚无已提交事件。</p> : <ol className="event-stream">{events.map((event) => <li key={event.id}><i/><div><b>{event.event_type}</b><small>{formatDateTime(event.occurred_at)} · v{event.aggregate_version}</small></div></li>)}</ol>}
      </section>
    </div>
    {inspector && <Inspector open kicker={inspector.kicker} title={inspector.title} tabs={inspector.tabs} onClose={() => setInspectorSelection(undefined)}/>}
    <ConfirmDialog open={Boolean(pendingDecision)} title={confirmation.title} detail={confirmation.detail} confirmLabel={confirmation.label} tone={pendingDecision?.startsWith("reject") || pendingDecision === "accept-candidate" ? "danger" : "warning"} pending={decisionPending} onCancel={() => setPendingDecision(undefined)} onConfirm={() => { if (pendingDecision) onDecision(pendingDecision); setPendingDecision(undefined); }}/>
  </div>;
}

function inspectorModel(selection: InspectorSelection | undefined, delivery: Delivery): { kicker: string; title: string; tabs: InspectorTab[] } | undefined {
  if (!selection) return undefined;
  if (selection.kind === "plan") {
    return {
      kicker: delivery.status === "awaiting_plan_decision" ? "人工闸门 · 等待决定" : "计划记录",
      title: "审查计划与执行边界",
      tabs: [
        { id: "summary", label: "摘要", content: <><h3>{delivery.requirements?.summary ?? "需求仍在规划中"}</h3><p>决定只绑定当前 Gate Subject 与聚合版本，后续修订不能复用。</p>{delivery.requirements && <ul className="inspector-list">{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code><span>{item.statement}</span></li>)}</ul>}</> },
        { id: "boundary", label: "边界", content: <><h3>{delivery.task?.title ?? "任务合同尚未生成"}</h3><p>{delivery.task?.instructions}</p><dl className="definition-list"><dt>允许修改</dt><dd>{delivery.task?.system_policy.allowed_paths.join(" · ") ?? "尚未确定"}</dd><dt>验收 ID</dt><dd>{delivery.task?.acceptance_ids.join(" · ") ?? "尚未确定"}</dd></dl></> },
        { id: "verification", label: "验证", content: <><h3>固定机器命令</h3><pre className="code-block">{delivery.task?.system_policy.verification_commands.join("\n") ?? "任务合同尚未提供验证命令"}</pre><dl className="definition-list"><dt>Gate Subject</dt><dd><code>{delivery.plan_gate?.subject_sha256 ?? "尚未生成"}</code></dd><dt>Revision</dt><dd>{delivery.plan_gate?.revision ?? "—"}</dd></dl></> },
      ],
    };
  }
  const record = selection.record;
  return {
    kicker: `${artifactTypeLabel(record.kind)} · ${statusLabel(record.status)}`,
    title: record.id,
    tabs: [
      { id: "summary", label: "摘要", content: <><h3>证据来源</h3><dl className="definition-list"><dt>交付</dt><dd>{record.delivery_id}</dd><dt>来源</dt><dd>{record.source_kind} / {record.source_id}</dd><dt>生产身份</dt><dd>{record.producer_identity}</dd><dt>创建时间</dt><dd>{formatDateTime(record.created_at)}</dd><dt>SHA-256</dt><dd><code>{record.content_sha256 ?? "未生成"}</code></dd></dl></> },
      { id: "payload", label: "载荷", content: <><h3>结构化载荷</h3><pre className="code-block">{JSON.stringify(record.payload ?? {}, null, 2)}</pre></> },
      { id: "verification", label: "验证", content: <><h3>完整性：{statusLabel(record.status)}</h3><dl className="definition-list"><dt>验证时间</dt><dd>{formatDateTime(record.verified_at ?? undefined)}</dd><dt>失败原因</dt><dd>{record.verification_error ?? "无"}</dd></dl></> },
    ],
  };
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

function formatDateTime(value?: string): string {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}
