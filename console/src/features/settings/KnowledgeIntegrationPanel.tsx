import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Select } from "antd";
import { DatabaseZap, ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import type { FeatureFlags } from "../../shared/features/api";
import {
  tenantKnowledgeKeys,
  type TenantConnection,
  type TenantConnectionCreate,
  type TenantProviderBinding,
  type TenantProviderBindingCreate,
  useConnectionSpaces,
  useKnowledgeIndexCatalog,
  useTenantBindings,
  useTenantConnections,
} from "../knowledge/tenantApi";

const initialConnection: TenantConnectionCreate = {
  provider_kind: "feishu",
  display_name: "",
  app_id_ref: "env:FEISHU_APP_ID",
  app_secret_ref: "env:FEISHU_APP_SECRET",
};

export function KnowledgeIntegrationPanel({ flags }: { flags?: FeatureFlags }) {
  const queryClient = useQueryClient();
  const gateAEnabled = Boolean(flags?.feishu_tenant_sync_v1);
  const gateBEnabled = Boolean(flags?.knowledge_hybrid_index_v1);
  const connections = useTenantConnections(gateAEnabled);
  const bindings = useTenantBindings(gateAEnabled);
  const catalog = useKnowledgeIndexCatalog(gateBEnabled);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [selectedSpaceId, setSelectedSpaceId] = useState("");
  const [connectionDraft, setConnectionDraft] = useState<TenantConnectionCreate>(initialConnection);
  const [bindingName, setBindingName] = useState("");
  const [rootNodeToken, setRootNodeToken] = useState("");
  const selectedConnection = connections.data?.find((item) => item.id === selectedConnectionId);
  const spaces = useConnectionSpaces(selectedConnectionId, selectedConnection?.status === "ready");

  useEffect(() => {
    if (!selectedConnectionId && connections.data?.length) {
      setSelectedConnectionId(connections.data[0].id);
    }
  }, [connections.data, selectedConnectionId]);

  useEffect(() => {
    if (spaces.data?.length && !spaces.data.some((space) => space.external_id === selectedSpaceId)) {
      setSelectedSpaceId(spaces.data[0].external_id);
    }
  }, [selectedSpaceId, spaces.data]);

  const refreshTenantData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.connections }),
      queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.bindings }),
      queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.catalog }),
    ]);
  };
  const createConnection = useMutation({
    mutationFn: (body: TenantConnectionCreate) => request<TenantConnection>("/v1/knowledge/connections", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: async (created) => {
      setConnectionDraft(initialConnection);
      setSelectedConnectionId(created.id);
      await refreshTenantData();
    },
  });
  const diagnose = useMutation({
    mutationFn: (connectionId: string) => request<TenantConnection>(`/v1/knowledge/connections/${encodeURIComponent(connectionId)}/diagnose`, { method: "POST" }),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.connections });
      await queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.spaces(updated.id) });
    },
  });
  const createBinding = useMutation({
    mutationFn: (body: TenantProviderBindingCreate) => request<TenantProviderBinding>("/v1/knowledge/provider-bindings-v2", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: async () => {
      setBindingName("");
      setRootNodeToken("");
      await queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.bindings });
    },
  });
  const refreshBinding = useMutation({
    mutationFn: (bindingId: string) => request<TenantProviderBinding>(`/v1/knowledge/provider-bindings-v2/${encodeURIComponent(bindingId)}/diagnose`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.bindings });
    },
  });
  const operationError = createConnection.error ?? diagnose.error ?? createBinding.error ?? refreshBinding.error;
  const activeIndexes = useMemo(
    () => catalog.data?.index_revisions.filter((revision) => revision.status === "active").length ?? 0,
    [catalog.data],
  );

  return <section className="panel knowledge-integration-panel evidence-rail">
    <div className="panel-head">
      <div><h2>飞书知识接入</h2><small>Tenant Service Principal · Project Approval · Immutable Index</small></div>
      <Button type="text" icon={<RefreshCw size={13}/>} onClick={refreshTenantData}>刷新接入状态</Button>
    </div>
    <div className="knowledge-ingress-rail" aria-label="知识接入权威链">
      <span><b>A</b> Tenant Connection</span><i>→</i><span><b>B</b> Project Approved Scope</span><i>→</i><span><b>C</b> Delivery Frozen Context</span>
    </div>
    {operationError && <ErrorState error={operationError}/>}
    {!flags ? <LoadingState label="正在解析知识接入 Gate…"/> : !gateAEnabled ? <EmptyState title="Gate A 尚未启用" detail="旧 v0.5.0 能力保持不变；启用 feishu_tenant_sync_v1 后才会挂载 Tenant Connection API。"/> : <div className="knowledge-integration-grid">
      <section className="knowledge-operation-card">
        <header><DatabaseZap size={18}/><div><h3>Tenant Connection</h3><p>只保存 env/keychain 凭据引用，不接收明文 Secret。</p></div></header>
        {connections.isLoading && <LoadingState label="正在读取 Tenant Connection…"/>}
        {connections.error && <ErrorState error={connections.error} retry={() => connections.refetch()}/>}
        <div className="knowledge-connection-list">
          {connections.data?.map((connection) => <article key={connection.id} data-selected={connection.id === selectedConnectionId} onClick={() => setSelectedConnectionId(connection.id)}>
            <div><b>{connection.display_name}</b><code>{connection.app_id_ref}</code></div>
            <StatusBadge value={connection.status}/>
            <Button size="small" loading={diagnose.isPending && diagnose.variables === connection.id} onClick={(event) => { event.stopPropagation(); diagnose.mutate(connection.id); }}>诊断连接</Button>
          </article>)}
        </div>
        <div className="compact-form">
          <label>连接名称<Input value={connectionDraft.display_name} onChange={(event) => setConnectionDraft((current) => ({ ...current, display_name: event.target.value }))} placeholder="例如：研发飞书"/></label>
          <label>App ID Reference<Input value={connectionDraft.app_id_ref} onChange={(event) => setConnectionDraft((current) => ({ ...current, app_id_ref: event.target.value }))}/></label>
          <label>App Secret Reference<Input value={connectionDraft.app_secret_ref} onChange={(event) => setConnectionDraft((current) => ({ ...current, app_secret_ref: event.target.value }))}/></label>
          <Button type="primary" disabled={!connectionDraft.display_name.trim()} loading={createConnection.isPending} onClick={() => createConnection.mutate(connectionDraft)}>创建连接</Button>
        </div>
      </section>

      <section className="knowledge-operation-card">
        <header><ExternalLink size={18}/><div><h3>可见 Space 与 Binding</h3><p>Binding 只能从最近一次 Tenant 权限探测可见的目录创建。</p></div></header>
        <Select aria-label="Tenant Connection" value={selectedConnectionId || undefined} onChange={setSelectedConnectionId} options={connections.data?.map((item) => ({ value: item.id, label: item.display_name }))} placeholder="选择已就绪连接"/>
        {spaces.isLoading && selectedConnectionId && <LoadingState label="正在读取飞书可见 Space…"/>}
        {spaces.error && <ErrorState error={spaces.error} retry={() => spaces.refetch()}/>}
        <div className="knowledge-space-probe-list">{spaces.data?.map((space) => <Button key={space.external_id} data-selected={space.external_id === selectedSpaceId} onClick={() => setSelectedSpaceId(space.external_id)}><b>{space.title}</b><code>{space.external_id}</code></Button>)}</div>
        <div className="compact-form">
          <label>Binding 名称<Input value={bindingName} onChange={(event) => setBindingName(event.target.value)} placeholder="例如：研发 Wiki"/></label>
          <label>Root Node Token（可选）<Input value={rootNodeToken} onChange={(event) => setRootNodeToken(event.target.value)} placeholder="留空表示整个 Space"/></label>
          <Button type="primary" disabled={!selectedConnectionId || !selectedSpaceId || !bindingName.trim()} loading={createBinding.isPending} onClick={() => createBinding.mutate({ connection_id: selectedConnectionId, display_name: bindingName.trim(), external_space_id: selectedSpaceId, root_node_token: rootNodeToken.trim() || null })}>冻结 Binding</Button>
        </div>
        <div className="knowledge-binding-list">{bindings.data?.map((binding) => <article key={binding.id}><div><b>{binding.display_name}</b><code>{binding.external_space_id}{binding.root_node_token ? ` / ${binding.root_node_token}` : ""}</code></div><StatusBadge value={binding.status}/><Button size="small" loading={refreshBinding.isPending && refreshBinding.variables === binding.id} onClick={() => refreshBinding.mutate(binding.id)}>刷新权限</Button></article>)}</div>
      </section>

      <section className="knowledge-operation-card knowledge-index-readiness">
        <header><ShieldCheck size={18}/><div><h3>Hybrid Index 资格链</h3><p>Profile、Embedding 资格、Policy、评测和激活 Revision 均不可变。</p></div></header>
        {!gateBEnabled && <EmptyState title="Gate B 尚未启用" detail="Tenant 同步可独立运行；只有 knowledge_hybrid_index_v1 开启后才读取本地 Ollama/Vector 资格。"/>}
        {catalog.isLoading && <LoadingState label="正在读取 Hybrid Index 目录…"/>}
        {catalog.error && <ErrorState error={catalog.error} retry={() => catalog.refetch()}/>}
        {catalog.data && <div className="knowledge-index-metrics">
          <span><b>{catalog.data.profiles.length}</b> Profile</span>
          <span><b>{catalog.data.qualifications.length}</b> 资格快照</span>
          <span><b>{catalog.data.retrieval_policies.length}</b> Retrieval Policy</span>
          <span><b>{catalog.data.evaluation_reports.filter((report) => report.status === "passed").length}</b> 通过评测</span>
        </div>}
        {gateBEnabled && catalog.data && <div className="knowledge-readiness-ledger">
          <div><b>Embedding Qualification</b>{catalog.data.qualifications.length ? catalog.data.qualifications.map((qualification) => <span key={qualification.id}><code>{qualification.model_name}</code><small>{qualification.dimension}d · {qualification.model_digest.slice(0, 22)}…</small><StatusBadge value={qualification.status}/></span>) : <small>尚无模型资格快照</small>}</div>
          <div><b>Immutable Index Revision</b>{catalog.data.index_revisions.length ? catalog.data.index_revisions.map((revision) => <span key={revision.id}><code>{revision.id}</code><small>{revision.document_count.toLocaleString("en-US")} docs · {revision.chunk_count.toLocaleString("en-US")} chunks · v{revision.version}</small><StatusBadge value={revision.status}/>{revision.capacity_status === "warning" && <StatusBadge value="warning"/>}</span>) : <small>尚无派生索引 Revision</small>}</div>
        </div>}
        <div className="knowledge-index-verdict"><span>活动索引 {activeIndexes}</span><StatusBadge value={activeIndexes > 0 ? "ready" : "not_ready"}/></div>
        <p className="field-help">活动索引只是 Gate B 就绪证据；Gate C 仍要求 Pipeline Artifact Contract 与 Delivery Context 冻结成功。</p>
      </section>
    </div>}
  </section>;
}
