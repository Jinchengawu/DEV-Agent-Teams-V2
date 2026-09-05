import { Button } from "antd";
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
      <div className="release-v2-head"><div><span className="eyebrow">external-forward-only-v1</span><h2>四仓 Release Surface</h2></div><StatusBadge value={health.data?.status ?? "healthy"}/></div>
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
  const snapshot = tree.workcell_run.workcell_snapshot;
  const verificationProfile = snapshot.workspace.verification_profile;
  const main = tree.agent_runs.find((item) => item.run_role === "main");
  const children = tree.agent_runs.filter((item) => item.run_role === "child");
  return <article className="workcell-run-card">
    <header><div><span className="workcell-key"><Bot size={15}/>{tree.workcell_run.workcell_key}</span><b>{tree.workcell_run.stage_path}</b><small>Loop {tree.workcell_run.loop_iteration} · Snapshot {tree.workcell_run.workcell_snapshot_sha256.slice(0, 10)}</small></div><div><StatusBadge value={tree.workcell_run.status}/>{cancellable && <Button type="text" danger icon={<CircleStop size={14}/>} loading={cancelPending} onClick={cancel}>取消 Workcell</Button>}</div></header>
    <div className="method-freeze-strip"><span>Method Pack</span><code>{snapshot.method_snapshot_sha256}</code><span>Repository</span><code>{snapshot.workspace.repository_uri}</code></div>
    <div className="method-freeze-strip"><span>冻结验证方案</span><code>{verificationProfile?.profile.id ?? "历史快照未冻结验证方案"}</code>{verificationProfile && <small>{verificationProfile.profile.commands.map((command) => command.join(" ")).join(" → ")} · 超时 {verificationProfile.profile.timeout_seconds} 秒</small>}</div>
    <div className="agent-tree">
      {main && <AgentNode tree={tree} agent={main} label="Main · planning + synthesis"/>}
      <div className="child-branch">
        {children.map((child) => <AgentNode key={child.id} tree={tree} agent={child} label={`Child · ${child.delegate_purpose}`}/>) }
      </div>
    </div>
    <footer><span>Machine Verification <StatusBadge value={tree.verification?.status ?? tree.result_validation?.status ?? "pending"}/></span><span>ReviewArtifact {tree.reviews.length}</span><span>Output Artifact {tree.result?.output_artifact_references.length ?? 0}</span>{tree.result?.candidate_sha && <code>Candidate {tree.result.candidate_sha.slice(0, 12)}</code>}</footer>
  </article>;
}

function AgentNode({ tree, agent, label }: { tree: WorkcellRunTree; agent: WorkcellRunTree["agent_runs"][number]; label: string }) {
  const attempts = tree.attempts.filter((item) => item.agent_run_id === agent.id);
  const method = agent.slot_key ? tree.workcell_run.workcell_snapshot.slot_method_bindings?.[agent.slot_key] : undefined;
  return <div className={`agent-node agent-${agent.run_role}`}><div className="agent-node-head"><span>{label}</span><StatusBadge value={agent.status}/></div><code>{agent.slot_key ?? "main"}{method ? ` · $${method}` : ""}</code><small>{agent.workspace_access} · depth {agent.depth}</small><small>Binding {agent.resolved_binding_hash.slice(0, 12)}</small><div className="attempt-list">{attempts.map((attempt) => <div key={attempt.id}><span>{attempt.phase} #{attempt.ordinal}</span><StatusBadge value={attempt.status}/><code>{attempt.result_artifact_sha256?.slice(0, 10) ?? "no result"}</code></div>)}</div></div>;
}
