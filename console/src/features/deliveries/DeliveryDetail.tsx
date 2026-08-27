import { useCallback, useState } from "react";
import { CheckCircle2, CircleAlert, Copy, GitCommitHorizontal, ShieldCheck } from "lucide-react";
import { artifactTypeLabel, identityLabel, statusLabel } from "../../i18n";
import type { Delivery, EvidenceRecord, ProductEvent } from "../../entities/delivery/model";
import { ConflictState, ErrorState } from "../../shared/feedback/AsyncState";
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
  decisionPending: boolean;
  decisionError?: Error | null;
  onDecision: (decision: DeliveryDecision) => void;
};

type InspectorSelection =
  | { kind: "plan" }
  | { kind: "candidate" }
  | { kind: "evidence"; record: EvidenceRecord };

export function DeliveryDetail({
  delivery,
  pipelineRun,
  pipelineError,
  events,
  eventsError,
  evidence,
  evidenceError,
  decisionPending,
  decisionError,
  onDecision,
}: Props) {
  const [selection, setSelection] = useState<InspectorSelection | null>(null);
  const [copyLabel, setCopyLabel] = useState("复制内容哈希");
  const closeInspector = useCallback(() => { setSelection(null); setCopyLabel("复制内容哈希"); }, []);
  const verified = evidence.filter((item) => item.status === "verified");
  const graphNodes = pipelineRun ? graphNodeProjections(pipelineRun.snapshot) : [];
  const narrative = statusNarrative(delivery);

  const copyHash = async (value?: string | null) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopyLabel("内容哈希已复制");
    } catch {
      setCopyLabel("浏览器未授权复制");
    }
  };

  const inspector = inspectorModel(selection, delivery);
  const inspectorFooter = selection?.kind === "plan" && delivery.status === "awaiting_plan_decision"
    ? <><button className="secondary" disabled={decisionPending} onClick={() => onDecision("reject-plan")}>退回计划</button><button className="primary" disabled={decisionPending} onClick={() => onDecision("approve-plan")}>{decisionPending ? "正在记录决定…" : "批准计划并开始执行"}</button></>
    : selection?.kind === "candidate" && delivery.status === "awaiting_candidate_decision"
      ? <><button className="secondary" disabled={decisionPending} onClick={() => onDecision("reject-candidate")}>退回候选</button><button className="primary" disabled={decisionPending} onClick={() => onDecision("accept-candidate")}>{decisionPending ? "正在记录决定…" : "接受候选并原子应用"}</button></>
      : selection?.kind === "evidence"
        ? <button className="secondary" disabled={!selection.record.content_sha256} onClick={() => copyHash(selection.record.content_sha256)}><Copy size={15}/>{copyLabel}</button>
        : <button className="secondary" onClick={closeInspector}>关闭</button>;

  return <div className="delivery-detail">
    <section className="run-hero surface-card">
      <div><p className="eyebrow">交付 {delivery.id.slice(0, 8)} · 真实运行</p><h2>{delivery.user_request}</h2><p className="run-meta">聚合版本 v{delivery.version} · Pipeline {delivery.pipeline_revision_id ?? delivery.journey_revision_id ?? "尚未绑定"} · 更新于 {formatDateTime(delivery.updated_at)}</p></div>
      <StatusBadge value={delivery.status}/>
    </section>

    <DeliveryStageRail delivery={delivery}/>
    <ConflictState error={decisionError}>
      {decisionError && <ErrorState error={decisionError}/>}
    </ConflictState>
    {delivery.error_code && <div className="repair-callout"><CircleAlert size={18}/><div><b>交付未能继续：{delivery.error_code}</b><span>修正需求或运行依赖后重新创建交付；失败运行不会覆盖沙箱主分支。</span></div></div>}

    <div className="run-room-grid">
      <div className="run-primary-column">
        <section className="surface-card current-stage-card">
          <div><p className="eyebrow">当前结论</p><h2>{narrative.conclusion}</h2><p>{narrative.reason}</p></div>
          <dl><dt>责任主体</dt><dd>{narrative.owner}</dd><dt>下一步</dt><dd>{narrative.next}</dd></dl>
        </section>

        <section className="surface-card artifact-panel">
          <div className="panel-head"><div><span>计划与授权</span><small>{identityLabel(delivery.planning_identity)}</small></div>{delivery.plan_gate && <button className={delivery.status === "awaiting_plan_decision" ? "primary screen-primary" : "secondary"} onClick={() => setSelection({ kind: "plan" })}>{delivery.status === "awaiting_plan_decision" ? "审查计划" : "检查计划"}</button>}</div>
          {delivery.requirements ? <><h3>{delivery.requirements.summary}</h3><ul className="acceptance-list">{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code><span>{item.statement}</span></li>)}</ul></> : <p className="muted">需求产物尚未生成。规划完成后才会出现可审批边界。</p>}
          {delivery.task && <div className="task-contract"><span>单一任务合同</span><b>{delivery.task.title}</b><small>{delivery.task.acceptance_ids.join(" · ")}</small></div>}
        </section>

        <section className="surface-card artifact-panel">
          <div className="panel-head"><div><span>候选变更与机器验证</span><small>{identityLabel(delivery.execution_identity ?? undefined)}</small></div>{delivery.candidate && <button className={delivery.status === "awaiting_candidate_decision" ? "primary screen-primary" : "secondary"} onClick={() => setSelection({ kind: "candidate" })}>{delivery.status === "awaiting_candidate_decision" ? "审查候选" : "检查候选"}</button>}</div>
          {delivery.candidate ? <><div className="revision-pair"><Revision label="基线" value={delivery.candidate.base_revision}/><GitCommitHorizontal size={19}/><Revision label="候选" value={delivery.candidate.candidate_revision}/></div><div className="diff-meta"><span>变更 {delivery.candidate.changed_files.length} 个文件</span><code>{delivery.candidate.diff_sha256}</code></div></> : <p className="muted">尚未形成经过固定测试验证的 Git Candidate。</p>}
          {delivery.verification && <div className={`verification ${delivery.verification.status === "passed" ? "verified" : "invalid"}`}><ShieldCheck size={18}/><div><b>固定机器测试：{statusLabel(delivery.verification.status)}</b><code>{delivery.verification.commands.join(" && ")}</code><small>退出码 {delivery.verification.exit_code} · 日志哈希 {delivery.verification.log_sha256}</small></div></div>}
          {delivery.apply_receipt && <div className="apply-receipt"><CheckCircle2 size={20}/><div><b>应用回执已核验</b><small>应用前 {delivery.apply_receipt.before_revision}<br/>候选 {delivery.apply_receipt.candidate_revision}<br/>应用后 {delivery.apply_receipt.after_revision}</small></div></div>}
        </section>
      </div>

      <aside className="run-context-column">
        <section className="surface-card run-context-card"><div className="panel-head"><span>运行上下文</span><small>真实聚合</small></div><dl><dt>规划身份</dt><dd>{identityLabel(delivery.planning_identity)}</dd><dt>执行身份</dt><dd>{identityLabel(delivery.execution_identity ?? undefined)}</dd><dt>证据身份</dt><dd>{delivery.evidence_identity}</dd><dt>Workspace</dt><dd>{delivery.workspace_id}</dd></dl></section>
        <section className="surface-card pipeline-ledger"><div className="panel-head"><span>Pipeline Run Ledger</span><small>{pipelineRun ? `GraphRun v${pipelineRun.version}` : "等待运行"}</small></div>
          {pipelineError && <ErrorState error={pipelineError}/>}
          {!pipelineRun && !pipelineError && <p className="muted">Pipeline Run 创建后，这里会显示真实图指纹与节点投影。</p>}
          {pipelineRun && <><dl><dt>状态</dt><dd>{pipelineRun.status}</dd><dt>图指纹</dt><dd><code>{pipelineRun.graph_fingerprint}</code></dd></dl><div className="pipeline-node-list">{graphNodes.map((node) => <article key={node.node_id}><i data-status={node.status}/><span><b>{node.node_id}</b><small>尝试 {node.attempt}</small></span><StatusBadge value={node.status}/></article>)}</div></>}
        </section>
      </aside>
    </div>

    <section className="evidence-spine">
      <div className="section-head"><div><p className="eyebrow">Evidence Spine</p><h2>事件与证据沿同一次交付对齐</h2><p>事件只说明已提交的状态变化；证据才承载可复算的来源与内容哈希。</p></div><span className="source-badge">{verified.length}/{evidence.length} 已验证</span></div>
      <div className="evidence-spine-grid">
        <div className="surface-card"><div className="panel-head"><span>产品事件</span><small>仅显示已提交事件</small></div>{eventsError && <ErrorState error={eventsError}/>} {events.length === 0 && !eventsError ? <p className="muted">尚无已提交事件。</p> : <ol className="event-stream">{events.map((event) => <li key={event.id ?? `${event.event_type}-${event.aggregate_version}`}><i/><div><b>{event.event_type}</b><small>{formatDateTime(event.occurred_at)} · 聚合 v{event.aggregate_version}</small></div></li>)}</ol>}</div>
        <div className="surface-card"><div className="panel-head"><span>可信证据</span><small>内容寻址</small></div>{evidenceError && <ErrorState error={evidenceError}/>} {evidence.length === 0 && !evidenceError ? <p className="muted">当前阶段尚无证据。系统不会为缺失产物补造记录。</p> : <div className="delivery-evidence-list">{evidence.map((record) => <button key={record.id} onClick={() => setSelection({ kind: "evidence", record })}><StatusBadge value={record.status}/><span><b>{artifactTypeLabel(record.kind)}</b><small>{record.producer_identity} · {record.source_id}</small></span><code>{record.content_sha256?.slice(0, 16) ?? "无可验证哈希"}</code></button>)}</div>}</div>
      </div>
    </section>

    {inspector && <Inspector open kicker={inspector.kicker} title={inspector.title} tabs={inspector.tabs} footer={inspectorFooter} onClose={closeInspector}/>}
  </div>;
}

function inspectorModel(selection: InspectorSelection | null, delivery: Delivery): { kicker: string; title: string; tabs: InspectorTab[] } | null {
  if (!selection) return null;
  if (selection.kind === "plan") {
    return {
      kicker: delivery.status === "awaiting_plan_decision" ? "人工闸门 · 等待决定" : "计划记录",
      title: "审查计划与执行边界",
      tabs: [
        { id: "summary", label: "摘要", content: <><h3>{delivery.requirements?.summary ?? "需求仍在规划中"}</h3><p>批准只绑定当前 Gate Subject 和聚合版本；后续版本不能复用本次决定。</p>{delivery.requirements && <ul className="inspector-list">{delivery.requirements.acceptance_criteria.map((item) => <li key={item.id}><code>{item.id}</code>{item.statement}</li>)}</ul>}</> },
        { id: "changes", label: "边界", content: <><h3>{delivery.task?.title ?? "任务合同尚未生成"}</h3><p>{delivery.task?.instructions}</p><dl className="definition-list"><dt>允许修改</dt><dd>{delivery.task?.system_policy.allowed_paths.join(" · ") ?? "尚未确定"}</dd><dt>验收 ID</dt><dd>{delivery.task?.acceptance_ids.join(" · ") ?? "尚未确定"}</dd></dl></> },
        { id: "verification", label: "验证", content: <><h3>固定机器命令</h3><pre className="code-block">{delivery.task?.system_policy.verification_commands.join("\n") ?? "任务合同尚未提供验证命令"}</pre><dl className="definition-list"><dt>Gate Subject</dt><dd><code>{delivery.plan_gate?.subject_sha256 ?? "尚未生成"}</code></dd><dt>Revision</dt><dd>{delivery.plan_gate?.revision ?? "—"}</dd></dl></> },
      ],
    };
  }
  if (selection.kind === "candidate") {
    return {
      kicker: delivery.status === "awaiting_candidate_decision" ? "人工闸门 · 等待决定" : "候选记录",
      title: "审查候选、Diff 与验证",
      tabs: [
        { id: "summary", label: "摘要", content: <><h3>{delivery.candidate?.changed_files.length ?? 0} 个文件进入候选</h3><dl className="definition-list"><dt>基线</dt><dd><code>{delivery.candidate?.base_revision}</code></dd><dt>候选</dt><dd><code>{delivery.candidate?.candidate_revision}</code></dd><dt>Diff SHA-256</dt><dd><code>{delivery.candidate?.diff_sha256}</code></dd></dl><ul className="inspector-list">{delivery.candidate?.changed_files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></> },
        { id: "changes", label: "变更", content: <><h3>Unified Diff</h3><pre className="code-block diff-block">{delivery.candidate?.unified_diff ?? "候选尚未生成"}</pre></> },
        { id: "verification", label: "验证", content: <><h3>固定测试：{statusLabel(delivery.verification?.status ?? "unknown")}</h3><pre className="code-block">{delivery.verification?.redacted_log || "验证日志尚未生成"}</pre><dl className="definition-list"><dt>命令</dt><dd>{delivery.verification?.commands.join(" && ")}</dd><dt>退出码</dt><dd>{delivery.verification?.exit_code ?? "—"}</dd><dt>日志哈希</dt><dd><code>{delivery.verification?.log_sha256}</code></dd></dl></> },
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

function statusNarrative(delivery: Delivery) {
  const values: Record<Delivery["status"], { conclusion: string; owner: string; reason: string; next: string }> = {
    queued: { conclusion: "交付已进入队列", owner: "机器控制", reason: "系统已记录真实交付请求，尚未开始规划。", next: "等待规划身份接手。" },
    planning: { conclusion: "正在收敛交付边界", owner: identityLabel(delivery.planning_identity), reason: "需求、验收条件与单一任务合同正在生成。", next: "形成 Gate Subject 后交给负责人审批。" },
    awaiting_plan_decision: { conclusion: "计划等待人工审批", owner: "当前审批负责人", reason: "需求与 TaskContract 已形成，但隔离执行尚未获得授权。", next: "检查边界、固定测试与 Gate Subject 后决定。" },
    executing: { conclusion: "候选正在隔离工作区生成", owner: identityLabel(delivery.execution_identity ?? undefined), reason: "代码执行已获计划授权，但变更尚未应用到主分支。", next: "等待执行完成并进入固定机器验证。" },
    verifying: { conclusion: "正在验证候选", owner: "机器验证器", reason: "固定命令正在检查候选与验收 ID。", next: "验证通过后生成候选审批主题。" },
    awaiting_candidate_decision: { conclusion: "候选等待人工决定", owner: "当前审批负责人", reason: "Diff 与机器验证已形成，主分支仍未写入。", next: "核对变更、测试与不可变哈希后决定。" },
    applying: { conclusion: "正在执行原子应用", owner: "机器控制", reason: "候选已获批准，系统正在通过 Git CAS 检查基线并应用。", next: "等待应用回执与最终证据。" },
    completed: { conclusion: "交付闭环已完成", owner: "机器控制", reason: "计划、候选、验证与应用结果均已记录。", next: "在下方检查事件与不可变证据。" },
    rejected: { conclusion: "候选或计划已被拒绝", owner: "人工审批", reason: "人工决定已记录，未批准的变更不会应用。", next: "根据退回原因创建新的交付运行。" },
    failed: { conclusion: "交付失败并保持受控", owner: "运行负责人", reason: delivery.error_code ? `失败代码：${delivery.error_code}` : "运行未能完成，候选不会污染主分支。", next: "修复依赖或目标边界后重新创建交付。" },
    cancelled: { conclusion: "交付已取消", owner: "发起取消的操作者", reason: "取消事件已经提交，系统停止继续推进。", next: "需要继续时创建新的交付运行。" },
  };
  return values[delivery.status];
}

function Revision({ label, value }: { label: string; value: string }) {
  return <div><small>{label} Revision</small><code>{value}</code></div>;
}

function formatDateTime(value?: string): string {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}
