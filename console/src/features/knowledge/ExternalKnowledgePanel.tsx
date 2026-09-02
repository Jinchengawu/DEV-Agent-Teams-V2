import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Select } from "antd";
import { BookOpenCheck, DatabaseZap, ExternalLink, RefreshCw, SearchCheck } from "lucide-react";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useFeatureFlags } from "../../shared/features/api";
import { useProject } from "../projects/api";
import {
  tenantKnowledgeKeys,
  type KnowledgeRetrievalResult,
  type KnowledgeSyncJob,
  useProjectBindingNodes,
  useProjectKnowledgeRetrievalOptions,
  useProjectKnowledgeSnapshots,
  useProjectKnowledgeSyncJobs,
} from "./tenantApi";

export function ExternalKnowledgePanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const featureFlags = useFeatureFlags();
  const gateAEnabled = Boolean(featureFlags.data?.feishu_tenant_sync_v1);
  const gateBEnabled = Boolean(featureFlags.data?.knowledge_hybrid_index_v1);
  const project = useProject(projectId, gateAEnabled);
  const approvals = useMemo(
    () => (project.data?.knowledge_source_approvals ?? []).filter((approval) => approval.enabled),
    [project.data?.knowledge_source_approvals],
  );
  const [bindingId, setBindingId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [retrievalPolicyId, setRetrievalPolicyId] = useState("");
  const [query, setQuery] = useState("");
  const [retrievalResult, setRetrievalResult] = useState<KnowledgeRetrievalResult>();

  useEffect(() => {
    if (bindingId && approvals.some((approval) => approval.binding_id === bindingId)) return;
    setBindingId(approvals[0]?.binding_id ?? "");
  }, [approvals, bindingId]);

  const selectedApproval = approvals.find((approval) => approval.binding_id === bindingId);
  const nodes = useProjectBindingNodes(projectId, bindingId, gateAEnabled);
  const jobs = useProjectKnowledgeSyncJobs(projectId, bindingId, gateAEnabled);
  const snapshots = useProjectKnowledgeSnapshots(projectId, bindingId, gateAEnabled);
  const retrievalOptions = useProjectKnowledgeRetrievalOptions(projectId, bindingId, gateBEnabled && Boolean(selectedApproval?.rag_enabled));

  const sourceNodes = useMemo(
    () => (nodes.data ?? []).filter((node) => node.kind === "document" && node.source_id),
    [nodes.data],
  );

  useEffect(() => {
    if (sourceId && sourceNodes.some((node) => node.source_id === sourceId)) return;
    setSourceId(sourceNodes[0]?.source_id ?? "");
  }, [sourceId, sourceNodes]);

  useEffect(() => {
    if (retrievalPolicyId && retrievalOptions.data?.some((option) => option.retrieval_policy_revision_id === retrievalPolicyId)) return;
    setRetrievalPolicyId(retrievalOptions.data?.[0]?.retrieval_policy_revision_id ?? "");
  }, [retrievalOptions.data, retrievalPolicyId]);

  useEffect(() => {
    setRetrievalResult(undefined);
  }, [bindingId]);

  const selectedNode = sourceNodes.find((node) => node.source_id === sourceId);
  const selectedSnapshot = snapshots.data?.find((snapshot) => snapshot.source_id === sourceId);
  const selectedJob = jobs.data?.find((job) => job.source_id === sourceId);

  const sync = useMutation({
    mutationFn: () => request<KnowledgeSyncJob>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-sync-jobs`, {
      method: "POST",
      body: JSON.stringify({
        binding_id: bindingId,
        source_id: sourceId,
        idempotency_key: stableSyncKey(bindingId, sourceId, selectedNode?.provider_revision),
      }),
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.syncJobs(projectId, bindingId) }),
        queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.snapshots(projectId, bindingId) }),
      ]);
    },
  });

  const preview = useMutation({
    mutationFn: () => request<KnowledgeRetrievalResult>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-retrieval-preview`, {
      method: "POST",
      body: JSON.stringify({
        provider_binding_id: bindingId,
        retrieval_policy_revision_id: retrievalPolicyId,
        query: query.trim(),
      }),
    }),
    onSuccess: setRetrievalResult,
  });

  const readError = featureFlags.error ?? project.error ?? nodes.error ?? jobs.error ?? snapshots.error ?? retrievalOptions.error;
  const operationError = sync.error ?? preview.error;

  return <section className="panel external-knowledge-panel evidence-rail">
    <div className="panel-head">
      <div><h2>外部知识快照与 RAG</h2><small>只消费项目批准的 Feishu Binding；同步产物和检索命中均保留不可变来源引用</small></div>
      <StatusBadge value={approvals.length ? "approved_scope" : "scope_required"}/>
    </div>

    {(featureFlags.isLoading || project.isLoading) && <LoadingState label="正在读取项目知识授权…"/>}
    {project.error && <ErrorState error={project.error} retry={() => project.refetch()}/>}
    {operationError && <ErrorState error={operationError}/>}

    {!featureFlags.isLoading && featureFlags.data && !gateAEnabled ? (
      <EmptyState title="Gate A 尚未启用" detail="当前运行实例不会请求 Feishu Tenant API；本地 Wiki 与历史知识保持可用。"/>
    ) : !project.isLoading && !project.error && approvals.length === 0 ? (
      <EmptyState title="项目尚未批准外部知识来源" detail="请由项目 Administrator 在项目治理页批准 Tenant Binding；RAG 还需单独启用。"/>
    ) : approvals.length > 0 ? <>
      <div className="external-knowledge-scope">
        <label>Approved Binding<Select aria-label="已批准知识来源" value={bindingId || undefined} onChange={setBindingId} options={approvals.map((approval) => ({ value: approval.binding_id, label: approval.binding_id }))}/></label>
        <span><b>Source Use</b><StatusBadge value={selectedApproval?.enabled ? "allowed" : "denied"}/></span>
        <span><b>RAG</b><StatusBadge value={selectedApproval?.rag_enabled ? "enabled" : "disabled"}/></span>
      </div>

      {readError && <ErrorState error={readError} retry={() => { void nodes.refetch(); void jobs.refetch(); void snapshots.refetch(); void retrievalOptions.refetch(); }}/>}
      <div className="external-knowledge-grid">
        <article className="knowledge-operation-card">
          <header><DatabaseZap size={18}/><div><h3>来源同步</h3><p>选择 Provider Node 后创建幂等 Sync Job；业务仓库不会被挂载或写入。</p></div></header>
          {nodes.isLoading ? <LoadingState label="正在读取已批准来源目录…"/> : sourceNodes.length ? <>
            <label>飞书文档<Select aria-label="飞书文档" value={sourceId || undefined} onChange={setSourceId} options={sourceNodes.map((node) => ({ value: node.source_id!, label: node.title }))}/></label>
            <div className="external-source-record">
              <b>{selectedNode?.title}</b>
              <code>{selectedNode?.source_id}</code>
              <small>Provider Revision {selectedNode?.provider_revision ?? "尚未返回"}</small>
            </div>
            <Button type="primary" className="button-icon" loading={sync.isPending} disabled={!bindingId || !sourceId} onClick={() => sync.mutate()}><RefreshCw size={15}/>同步当前来源</Button>
            <div className="external-source-state">
              <span>最近任务 <StatusBadge value={selectedJob?.status ?? "not_run"}/></span>
              <span>最新快照 <StatusBadge value={selectedSnapshot ? "available" : "missing"}/></span>
            </div>
            {selectedJob?.error_code && <p className="field-help">失败码 <code>{selectedJob.error_code}</code></p>}
            {(jobs.data?.length ?? 0) > 0 && <div className="external-history-list" aria-label="同步任务历史">{jobs.data?.map((job) => <div key={job.id}><span><b>{job.source_id}</b><code>{job.id}</code></span><StatusBadge value={job.status}/><small>attempt {job.attempt}/{job.max_attempts}</small></div>)}</div>}
          </> : <EmptyState title="Binding 中没有可同步文档" detail="先在设置页刷新 Provider 权限与目录；文件夹本身不会生成知识快照。"/>}
        </article>

        <article className="knowledge-operation-card">
          <header><BookOpenCheck size={18}/><div><h3>不可变 Snapshot</h3><p>快照绑定 Source、Provider Revision、Artifact SHA-256 与抓取身份。</p></div></header>
          {snapshots.isLoading ? <LoadingState label="正在读取来源快照…"/> : selectedSnapshot ? <><div className="external-snapshot-card">
            <span>Snapshot <code>{selectedSnapshot.id}</code></span>
            <span>Revision <code>{selectedSnapshot.provider_revision}</code></span>
            <span>Artifact <code>{selectedSnapshot.artifact.sha256.slice(0, 20)}…</code></span>
            <span>Fetched by <code>{selectedSnapshot.fetched_by_product_user_id}</code></span>
            {selectedSnapshot.source_url && <a href={selectedSnapshot.source_url} target="_blank" rel="noreferrer"><ExternalLink size={13}/>打开来源</a>}
          </div><div className="external-history-list" aria-label="Snapshot 历史">{snapshots.data?.map((snapshot) => <div key={snapshot.id}><span><b>{snapshot.source_id}</b><code>{snapshot.id}</code></span><StatusBadge value={snapshot.id === selectedSnapshot.id ? "current" : "historical"}/><small>{snapshot.provider_revision}</small></div>)}</div></> : <EmptyState title="当前来源尚无快照" detail="创建同步任务并等待 Worker 成功后，快照证据会出现在这里。"/>}
        </article>

        <article className="knowledge-operation-card external-rag-preview">
          <header><SearchCheck size={18}/><div><h3>RAG 检索预览</h3><p>服务端根据当前身份、Project Role 与 Approved Source Scope 编译允许集合。</p></div></header>
          {!selectedApproval?.rag_enabled ? <EmptyState title="RAG 未获项目授权" detail="来源可用于显式上下文并不自动允许 RAG；请由项目管理员单独开启。"/> : retrievalOptions.isLoading ? <LoadingState label="正在读取已评测检索策略…"/> : retrievalOptions.data?.length ? <>
            <label>Retrieval Policy<Select aria-label="Retrieval Policy" value={retrievalPolicyId || undefined} onChange={setRetrievalPolicyId} options={retrievalOptions.data.map((option) => ({ value: option.retrieval_policy_revision_id, label: `${option.retrieval_policy_revision_id} · ${option.index_revision_id}` }))}/></label>
            <div className="external-source-record"><b>Server-compiled Retrieval Option</b><code>{retrievalOptions.data.find((option) => option.retrieval_policy_revision_id === retrievalPolicyId)?.index_revision_id}</code><small>Index Profile {retrievalOptions.data.find((option) => option.retrieval_policy_revision_id === retrievalPolicyId)?.index_profile_revision_id}</small></div>
            <label>检索查询<Input.TextArea aria-label="检索查询" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入需要核对的工程知识问题" autoSize={{ minRows: 2, maxRows: 4 }}/></label>
            <Button type="primary" className="button-icon" loading={preview.isPending} disabled={!query.trim() || !retrievalPolicyId} onClick={() => preview.mutate()}><SearchCheck size={15}/>运行 RAG 预览</Button>
            {retrievalResult && <div className="rag-preview-results" aria-label="RAG 检索结果">
              <div className="retrieval-receipt"><b>Retrieval Receipt</b><code>{retrievalResult.receipt.id}</code><small>Allowed Set {retrievalResult.receipt.allowed_source_set_sha256.slice(0, 16)}…</small></div>
              {retrievalResult.hits.length ? retrievalResult.hits.map((hit) => <article key={hit.citation_id}><div><b>{hit.title || hit.source_id}</b><code>{hit.citation_id}</code></div><p>{hit.content}</p>{hit.source_url && <a href={hit.source_url} target="_blank" rel="noreferrer">来源链接</a>}</article>) : <EmptyState title="允许范围内没有命中" detail={retrievalResult.receipt.empty_reason ?? "系统不会用未批准来源补齐空结果。"}/>}
            </div>}
          </> : <EmptyState title="没有可用的已评测检索策略" detail="必须先完成 Hybrid Index 构建、评测通过和 Revision 激活；系统会 Fail Closed。"/>}
        </article>
      </div>
    </> : null}
  </section>;
}

function stableSyncKey(bindingId: string, sourceId: string, providerRevision?: string | null) {
  const value = `${bindingId}\u0000${sourceId}\u0000${providerRevision ?? "unknown"}`;
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `console-sync-v1:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}
