import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Link2, Play, Power, RefreshCw } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";

type Deployment = components["schemas"]["AgentDeployment"];
type Profile = components["schemas"]["AgentProfile"];
type Instance = components["schemas"]["AgentInstance"];
type Provider = components["schemas"]["ProviderManifestView"];

export function AgentDeploymentsPanel() {
  const cache = useQueryClient();
  const deployments = useQuery({ queryKey: ["agent-deployments"], queryFn: () => request<Deployment[]>("/v1/agent-deployments") });
  const profiles = useQuery({ queryKey: ["agent-profiles"], queryFn: () => request<Profile[]>("/v1/agent-profiles") });
  const instances = useQuery({ queryKey: ["agents"], queryFn: () => request<Instance[]>("/v1/agent-instances") });
  const providers = useQuery({ queryKey: ["provider-manifests"], queryFn: () => request<Provider[]>("/v1/provider-manifests") });
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [profileId, setProfileId] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [pendingDisable, setPendingDisable] = useState<Deployment>();
  const publishedProfiles = useMemo(() => (profiles.data ?? []).filter((item) => item.latest_revision), [profiles.data]);
  const readyInstances = useMemo(() => (instances.data ?? []).filter((item) => item.enabled && item.health?.status === "ready"), [instances.data]);
  const refresh = () => cache.invalidateQueries({ queryKey: ["agent-deployments"] });
  const create = useMutation({
    mutationFn: () => {
      const profile = publishedProfiles.find((item) => item.id === profileId);
      return request<Deployment>("/v1/agent-deployments", { method: "POST", body: JSON.stringify({ id, name, profile_id: profileId, profile_revision: profile?.latest_revision, instance_id: instanceId, provider_id: providerId }) });
    },
    onSuccess: async () => { setId(""); setName(""); await refresh(); },
  });
  const command = useMutation({
    mutationFn: ({ item, action }: { item: Deployment; action: "qualify" | "enable" | "disable" }) => request<Deployment>(`/v1/agent-deployments/${item.id}/${action}`, { method: "POST", body: JSON.stringify({ expected_version: item.version }) }),
    onSuccess: refresh,
  });
  const refreshSnapshot = useMutation({
    mutationFn: async (item: Deployment) => {
      const updated = await request<Deployment>(`/v1/agent-deployments/${item.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: item.version, instance_id: item.instance_id }) });
      return request<Deployment>(`/v1/agent-deployments/${item.id}/qualify`, { method: "POST", body: JSON.stringify({ expected_version: updated.version }) });
    },
    onSuccess: refresh,
  });
  if (deployments.isLoading || profiles.isLoading || instances.isLoading || providers.isLoading) return <section className="panel"><LoadingState label="正在读取 Agent 部署与 Provider…"/></section>;
  const queryError = deployments.error || profiles.error || instances.error || providers.error;
  if (queryError) return <section className="panel"><ErrorState error={queryError}/></section>;
  const items = deployments.data ?? [];
  return <section className="panel agent-deployments-panel">
    <div className="panel-head"><span>Agent 部署</span><small>已发布角色 → 运行实例 → ACWM Provider</small></div>
    <div className="deployment-workbench"><div className="deployment-list">
      {items.map((item) => {
        const snapshotStale = item.qualification_errors.some((error) => error.endsWith("_STALE"));
        return <article key={item.id} className="binding-card"><div><span className="eyebrow">{item.provider_id}</span><h3>{item.name}</h3><p>{item.profile_id} · R{item.profile_revision} → {item.instance_id}</p></div><StatusBadge value={item.enabled ? "ready" : item.qualification_status === "failed" ? "failed" : "unknown"}/><dl><dt>资格状态</dt><dd>{qualificationLabel(item.qualification_status)}</dd><dt>隔离</dt><dd>{item.isolation_mode === "dedicated" ? "独占实例" : "共享实例、会话隔离"}</dd><dt>绑定哈希</dt><dd><code>{item.provider_fingerprint.slice(0, 16)}</code></dd></dl>{item.qualification_errors.length > 0 && <div className="validation-errors">{item.qualification_errors.map((error) => <span key={error}>{error}</span>)}</div>}<div className="row-actions">{snapshotStale && <button disabled={refreshSnapshot.isPending} onClick={() => refreshSnapshot.mutate(item)}><RefreshCw size={14}/>刷新快照并重新检查</button>}<button disabled={refreshSnapshot.isPending} onClick={() => command.mutate({ item, action: "qualify" })}><CheckCircle2 size={14}/>资格检查</button><button disabled={item.qualification_status !== "qualified" || refreshSnapshot.isPending} onClick={() => item.enabled ? setPendingDisable(item) : command.mutate({ item, action: "enable" })}>{item.enabled ? <Power size={14}/> : <Play size={14}/>} {item.enabled ? "禁用新运行" : "启用部署"}</button></div></article>;
      })}
      {items.length === 0 && <EmptyState title="尚未创建 Agent 部署" detail="先发布角色并完成运行实例健康检查，再创建可执行部署。"/>}
    </div><div className="compact-form deployment-create"><h3>创建 Deployment</h3><label>Deployment ID<input value={id} onChange={(event) => setId(event.target.value)} placeholder="例如 frontend-codex"/></label><label>中文名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 前端 Codex 部署"/></label><label>已发布 Agent 角色<select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">请选择</option>{publishedProfiles.map((item) => <option key={item.id} value={item.id}>{item.name} · R{item.latest_revision}</option>)}</select></label><label>已就绪运行实例<select value={instanceId} onChange={(event) => setInstanceId(event.target.value)}><option value="">请选择</option>{readyInstances.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.runtime_type}</option>)}</select></label><label>ACWM Provider<select value={providerId} onChange={(event) => setProviderId(event.target.value)}><option value="">请选择</option>{(providers.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.id} · {item.capabilities.map((capability) => capability.id).join("、")}</option>)}</select></label><p className="field-help">创建后必须通过资格检查并显式启用，才会出现在编排节点的可选列表中。</p><button className="primary button-icon" disabled={!id || !name || !profileId || !instanceId || !providerId || create.isPending} onClick={() => create.mutate()}><Link2 size={15}/>创建部署</button>{(create.error || command.error || refreshSnapshot.error) && <ErrorState error={(create.error || command.error || refreshSnapshot.error)!}/>}</div></div>
    <ConfirmDialog open={Boolean(pendingDisable)} title={`禁用 Agent 部署“${pendingDisable?.name ?? ""}”`} detail="禁用后，该部署不会再出现在新流水线 Assignment 的可选列表中，也不能用于新 Agent Run；历史 Revision 的冻结快照和证据保持不变。" confirmLabel="确认禁用部署" tone="danger" pending={command.isPending} onCancel={() => setPendingDisable(undefined)} onConfirm={() => { if (pendingDisable) command.mutate({ item: pendingDisable, action: "disable" }, { onSuccess: () => setPendingDisable(undefined) }); }}/>
  </section>;
}

function qualificationLabel(status: Deployment["qualification_status"]) { return status === "qualified" ? "已通过" : status === "failed" ? "未通过" : "待检查"; }
