import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Plus } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { identityLabel, runtimeTypeLabel } from "../../i18n";
import { AgentProfilesPanel } from "./AgentProfilesPanel";
import { AgentDeploymentsPanel } from "./AgentDeploymentsPanel";

type Instance = components["schemas"]["AgentInstance"];
type RuntimeType = Instance["runtime_type"];
export function AgentsPage() {
  const client = useQueryClient();
  const instances = useQuery({ queryKey: ["agents"], queryFn: () => request<Instance[]>("/v1/agent-instances") });
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<RuntimeType>("codex-cli");
  const [connectionTarget, setConnectionTarget] = useState("codex");
  const [credentialRef, setCredentialRef] = useState("");
  const create = useMutation({
    mutationFn: () => request<Instance>("/v1/agent-instances", { method: "POST", body: JSON.stringify({ name, runtime_type: runtime, connection: runtime === "codex-cli" ? { command: connectionTarget } : { endpoint: connectionTarget }, credential_ref: credentialRef || null }) }),
    onSuccess: async () => { setName(""); setCredentialRef(""); await client.invalidateQueries({ queryKey: ["agents"] }); },
  });
  const health = useMutation({ mutationFn: (id: string) => request<Instance>(`/v1/agent-instances/${id}/health-check`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });
  const toggle = useMutation({ mutationFn: (instance: Instance) => request<Instance>(`/v1/agent-instances/${instance.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: instance.version, enabled: !instance.enabled }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });
  if (instances.isLoading) return <LoadingState label="正在读取智能体运行实例…"/>;
  if (instances.error) return <ErrorState error={instances.error} retry={() => { void instances.refetch(); }}/>;
  const registered = instances.data ?? [];

  return <div className="agents-layout">
    <AgentProfilesPanel/>
    <AgentDeploymentsPanel/>
    <section className="panel"><div className="panel-head"><span>运行实例</span><small>{registered.length} 个已注册</small></div><div className="instance-grid">{registered.map((item) => {
      const currentHealth = item.health ?? { status: "unknown" as const };
      return <article key={item.id}><div className="instance-icon"><Bot size={20}/></div><div className="instance-main"><div><span className="eyebrow">{runtimeTypeLabel(item.runtime_type)}</span><h3>{item.name}</h3></div><StatusBadge value={item.enabled ? currentHealth.status : "cancelled"}/><dl><dt>运行身份</dt><dd>{identityLabel(currentHealth.identity ?? undefined)}</dd><dt>连接配置</dt><dd>{connectionLabel(item)}</dd><dt>能力特征</dt><dd>{item.features.join(" · ") || "未声明"}</dd><dt>配置版本</dt><dd>{item.version}</dd><dt>凭据</dt><dd>{item.credential_ref || "不需要凭据引用"}</dd></dl><div className="row-actions"><button onClick={() => health.mutate(item.id)}><Activity size={14}/>健康检查</button><button onClick={() => toggle.mutate(item)}>{item.enabled ? "禁用新运行" : "重新启用"}</button></div></div></article>;
    })}</div>{registered.length === 0 && <EmptyState title="尚未注册运行实例" detail="先在右侧注册实例，完成健康检查后才能绑定能力。"/>}{(health.error || toggle.error) && <ErrorState error={(health.error || toggle.error)!}/>}</section>

    <section className="panel compact-form"><div className="panel-head"><span>注册实例</span><small>只保存凭据引用</small></div><label>实例名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Codex 后端执行器 01"/></label><label>运行时类型<select value={runtime} onChange={(event) => { const next = event.target.value as RuntimeType; setRuntime(next); setConnectionTarget(next === "codex-cli" ? "codex" : ""); create.reset(); }}><option value="codex-cli">Codex 命令行</option><option value="hermes-http">Hermes HTTP 服务</option><option value="hermes-acp">Hermes ACP 服务</option></select></label><label>{runtime === "codex-cli" ? "命令" : "连接端点"}<input value={connectionTarget} onChange={(event) => setConnectionTarget(event.target.value)} placeholder={runtime === "codex-cli" ? "例如：codex" : "例如：http://127.0.0.1:9000"}/></label><label>凭据引用<input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} placeholder="env:HERMES_API_KEY 或 keychain:名称"/></label><p className="field-help">密钥值不会进入 API、日志或 SQLite。仅接受环境变量或系统钥匙串引用。</p><button className="primary button-icon" disabled={!name.trim() || !connectionTarget.trim() || create.isPending} onClick={() => create.mutate()}><Plus size={16}/>注册实例</button>{create.error && <ErrorState error={create.error}/>}</section>

  </div>;
}

function connectionLabel(instance: Instance) {
  return instance.runtime_type === "codex-cli" ? instance.connection.command ?? "未配置命令" : instance.connection.endpoint ?? "未配置端点";
}
