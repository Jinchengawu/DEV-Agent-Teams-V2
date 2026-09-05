import { useState } from "react";
import { Button, Collapse, Modal } from "antd";
import { Bot, CircleStop, ExternalLink, GitPullRequest, RotateCw, ShieldCheck } from "lucide-react";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import {
  type WorkcellRunTree,
  useCancelWorkcellRun,
  useDeliveryWorkcellRuns,
  useExternalRelease,
  useReleaseHealth,
  useResumeForward,
  useWorkcellArtifact,
} from "./api";

export function WorkcellExecutionPanel({ deliveryId, projectId }: { deliveryId: string; projectId: string }) {
  const runs = useDeliveryWorkcellRuns(deliveryId);
  const release = useExternalRelease(deliveryId);
  const health = useReleaseHealth(projectId);
  const cancel = useCancelWorkcellRun(deliveryId);
  const resume = useResumeForward(projectId, deliveryId);
  if (runs.isLoading) return <section className="panel"><LoadingState label="正在读取 Workcell/Main/Child/Attempt Tree…"/></section>;
  if (runs.error) return <section className="panel"><ErrorState error={runs.error} retry={() => runs.refetch()}/></section>;
  const trees = runs.data ?? [];
  const activeStatuses = new Set(["planning", "delegating", "verifying", "reviewing", "synthesizing"]);
  return <div className="workcell-observability">
    <section className="panel workcell-run-panel">
      <div className="panel-head"><span>Main / Child / Attempt Tree</span><small>所有 Child 均来自 Published Pipeline 的冻结 Slot，深度最多 1</small></div>
      {trees.length === 0 ? <p className="workcell-empty">Stage 尚未创建 WorkcellRun。只有真实 Pipeline Attempt 启动后才会出现节点。</p> : <div className="workcell-run-list">{trees.map((tree) => <WorkcellTree key={tree.workcell_run.id} tree={tree} cancel={() => cancel.mutate({ runId: tree.workcell_run.id!, expectedVersion: tree.workcell_run.version })} cancelPending={cancel.isPending} cancellable={activeStatuses.has(tree.workcell_run.status)}/>)}</div>}
      {cancel.error && (
        <ErrorState error={cancel.error}/>
      )}
    </section>
    <section className="panel external-release-panel">
      <div className="release-v2-head"><div><span className="eyebrow">external-forward-only-v1</span><h2>四仓 Release Surface</h2></div><StatusBadge value={health.data?.status ?? "pending"}/></div>
      {release.isLoading ? <LoadingState label="正在读取 Candidate、PR 与 Apply Receipt…"/> : release.error ? <ErrorState error={release.error} retry={() => release.refetch()}/> : <div className="release-candidate-ledger">{release.data?.candidates.length ? release.data.candidates.map((candidate) => {
        const pull = release.data?.pull_requests.find((item) => item.candidate_id === candidate.id);
        const receipt = release.data?.remote_apply_receipts.find((item) => item.candidate_id === candidate.id);
        return <article key={candidate.id}><header><b>{candidate.workcell_key}</b><StatusBadge value={receipt ? "applied" : "verified"}/></header><dl><dt>Candidate</dt><dd><code>{candidate.candidate_revision.slice(0, 12)}</code></dd><dt>Verification</dt><dd><code>{candidate.verification_sha256.slice(0, 12)}</code></dd><dt>Review</dt><dd>{candidate.review_artifact_ids.length} 份</dd><dt>Pull Request</dt><dd>{pull ? <a href={pull.url} target="_blank" rel="noreferrer"><GitPullRequest size={12}/>#{pull.pull_request_id} · {pull.state}<ExternalLink size={11}/></a> : "等待创建"}</dd><dt>Remote main</dt><dd>{receipt ? <code>{receipt.after_revision.slice(0, 12)}</code> : "尚未 Apply"}</dd></dl></article>;
      }) : <p className="workcell-empty">尚未形成完整 Candidate 集合。Release Gate 不会提前出现。</p>}</div>}
      {release.data?.bundle && <div className="release-hash-strip"><span>ReleaseBundleV2</span><code>{release.data.bundle.bundle_sha256}</code></div>}
      {release.data?.manifest && <div className="release-hash-strip verified"><ShieldCheck size={15}/><span>ReleaseManifestV2</span><code>{release.data.manifest.manifest_sha256}</code></div>}
      {health.data?.status === "release_drifted" && <div className="release-drift-alert" role="alert"><div><b>Partial Apply 需要人工继续</b><p>{health.data.error_code}。已成功仓库不会回滚；仅在已应用仓库仍为 Candidate、未应用仓库仍为 Base 时允许继续。</p></div><Button danger icon={<RotateCw size={14}/>} disabled={release.data?.apply_attempt?.status !== "needs_attention"} loading={resume.isPending} onClick={() => resume.mutate()}>Resume forward</Button></div>}
      {(health.error || resume.error) && (
        <ErrorState error={health.error ?? resume.error!}/>
      )}
    </section>
  </div>;
}

function WorkcellTree({ tree, cancel, cancelPending, cancellable }: { tree: WorkcellRunTree; cancel: () => void; cancelPending: boolean; cancellable: boolean }) {
  const [selectedArtifact, setSelectedArtifact] = useState<string>();
  const snapshot = tree.workcell_run.workcell_snapshot;
  const verificationProfile = snapshot.workspace.verification_profile;
  const main = tree.agent_runs.find((item) => item.run_role === "main");
  const children = tree.agent_runs.filter((item) => item.run_role === "child");
  const artifacts = new Map<string, string>();
  for (const agent of tree.agent_runs) for (const item of agent.artifact_envelopes) {
    if (item.reference) artifacts.set(item.reference.sha256, item.contract_id === "workspace-candidate-diff-v1" ? "Candidate Diff" : agent.delegate_purpose === "review" ? "Review 原始输出" : item.contract_id);
  }
  for (const review of tree.reviews) artifacts.set(review.artifact_reference.sha256, "Review 原始输出");
  const reviewError = tree.workcell_run.error_code?.startsWith("WORKCELL_REVIEW");
  return <article className="workcell-run-card">
    <header><div><span className="workcell-key"><Bot size={15}/>{tree.workcell_run.workcell_key}</span><b>{tree.workcell_run.stage_path}</b><small>Loop {tree.workcell_run.loop_iteration} · Snapshot {tree.workcell_run.workcell_snapshot_sha256.slice(0, 10)}</small></div><div><StatusBadge value={tree.workcell_run.status}/>{cancellable && <Button type="text" danger icon={<CircleStop size={14}/>} loading={cancelPending} onClick={cancel}>取消 Workcell</Button>}</div></header>
    <div className="method-freeze-strip"><span>Method Pack</span><code>{snapshot.method_snapshot_sha256}</code><span>Repository</span><code>{snapshot.workspace.repository_uri}</code></div>
    <div className="method-freeze-strip"><span>冻结验证方案</span><code>{verificationProfile?.profile.id ?? "历史快照未冻结验证方案"}</code>{verificationProfile && <small>{verificationProfile.profile.commands.map((command) => command.join(" ")).join(" → ")} · 超时 {verificationProfile.profile.timeout_seconds} 秒</small>}</div>
    {tree.workcell_run.error_code && <div role="alert" className="repair-callout"><b>{reviewError ? "Review 输出无效，当前轮次未通过" : "当前轮次未通过"}</b><code>{tree.workcell_run.error_code}</code><p>{reviewError ? "原始输出已保留；由流水线的有界修复创建新轮次，达到上限后需重新审查任务。" : "查看本轮验证与 Attempt 证据，按交付状态执行合法操作。"}</p></div>}
    {snapshot.review_scope && <Collapse items={[{ key: "scope", label: "本仓冻结验收责任", children: <><code>{snapshot.review_scope.sha256}</code><ul>{snapshot.review_scope.acceptance.map((item) => <li key={item.acceptance_id}><b>{item.acceptance_id}</b> {item.statement}<p>{item.responsibility}</p></li>)}</ul><ul>{snapshot.review_scope.system_policies.map((item) => <li key={item.id}><code>{item.id}</code> {item.statement}</li>)}</ul></> }]}/>}
    <div className="agent-tree">
      {main && <AgentNode tree={tree} agent={main} label="Main · planning + synthesis"/>}
      <div className="child-branch">
        {children.map((child) => <AgentNode key={child.id} tree={tree} agent={child} label={`Child · ${child.delegate_purpose}`}/>) }
      </div>
    </div>
    {tree.verification && <Collapse items={[{ key: "verification", label: `机器验证报告：${tree.verification.status}`, children: <pre className="code-block">{JSON.stringify(tree.verification.report, null, 2)}</pre> }]}/>}
    {tree.reviews.length > 0 && <Collapse items={tree.reviews.map((review) => ({ key: review.id!, label: `Review：${review.blocking_findings?.length ? `${review.blocking_findings.length} 项阻断` : "已接纳，无阻断"}`, children: <ul>{review.blocking_findings?.map((finding, index) => <li key={`${finding.code}-${index}`}><code>{finding.acceptance_id ?? finding.system_policy_id ?? "历史未声明归属"}</code> {finding.summary}</li>)}</ul> }))}/>}
    {artifacts.size > 0 && <div aria-label="本轮证据正文">{[...artifacts].map(([sha, label]) => <Button key={sha} type="text" onClick={() => setSelectedArtifact(sha)}>查看 {label} · {sha.slice(0, 8)}</Button>)}</div>}
    {selectedArtifact && <WorkcellArtifactDialog deliveryId={tree.workcell_run.delivery_id} runId={tree.workcell_run.id!} sha256={selectedArtifact} close={() => setSelectedArtifact(undefined)}/>}
    <footer><span>Machine Verification <StatusBadge value={tree.verification?.status ?? tree.result_validation?.status ?? "pending"}/></span><span>ReviewArtifact {tree.reviews.length}</span><span>Output Artifact {tree.result?.output_artifact_references.length ?? 0}</span>{tree.result?.candidate_sha && <code>Candidate {tree.result.candidate_sha.slice(0, 12)}</code>}</footer>
  </article>;
}

function WorkcellArtifactDialog({ deliveryId, runId, sha256, close }: { deliveryId: string; runId: string; sha256: string; close: () => void }) {
  const artifact = useWorkcellArtifact(deliveryId, runId, sha256);
  let content = artifact.data?.content;
  if (content && artifact.data?.reference.media_type.includes("json")) {
    try {
      const parsed: unknown = JSON.parse(content);
      content = parsed && typeof parsed === "object" && "diff_content" in parsed && typeof parsed.diff_content === "string" ? parsed.diff_content : JSON.stringify(parsed, null, 2);
    } catch { /* 已由服务端验证；保留原始纯文本，不执行内容。 */ }
  }
  return <Modal open title="已登记证据正文" footer={null} onCancel={close} width={900}>
    <code>{sha256}</code>
    {artifact.isLoading ? <LoadingState label="正在验证并读取产物…"/> : artifact.error ? <ErrorState error={artifact.error}/> : <pre className="code-block">{content}</pre>}
  </Modal>;
}

function AgentNode({ tree, agent, label }: { tree: WorkcellRunTree; agent: WorkcellRunTree["agent_runs"][number]; label: string }) {
  const attempts = tree.attempts.filter((item) => item.agent_run_id === agent.id);
  const method = agent.slot_key ? tree.workcell_run.workcell_snapshot.slot_method_bindings?.[agent.slot_key] : undefined;
  return <div className={`agent-node agent-${agent.run_role}`}><div className="agent-node-head"><span>{label}</span><StatusBadge value={agent.status}/></div><code>{agent.slot_key ?? "main"}{method ? ` · $${method}` : ""}</code><small>{agent.workspace_access} · depth {agent.depth}</small><small>Binding {agent.resolved_binding_hash.slice(0, 12)}</small><div className="attempt-list">{attempts.map((attempt) => <div key={attempt.id}><span>{attempt.phase} #{attempt.ordinal}</span><StatusBadge value={attempt.status}/><code>{attempt.result_artifact_sha256?.slice(0, 10) ?? "no result"}</code>{attempt.error_code && <code>{attempt.error_code}</code>}</div>)}</div></div>;
}
