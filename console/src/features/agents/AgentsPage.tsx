import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Plus } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { identityLabel, runtimeTypeLabel } from "../../i18n";

type Instance = components["schemas"]["AgentInstance"];
type RuntimeType = Instance["runtime_type"];

export function AgentsPage() {
  const client = useQueryClient();
  const instances = useQuery({ queryKey: ["agents"], queryFn: () => request<Instance[]>("/v1/agent-instances") });
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<RuntimeType>("codex-cli");
  const [credentialRef, setCredentialRef] = useState("");
  const create = useMutation({
    mutationFn: () => request<Instance>("/v1/agent-instances", { method: "POST", body: JSON.stringify({ name, runtime_type: runtime, connection: runtime === "codex-cli" ? { command: "codex" } : { endpoint: "http://127.0.0.1:9000" }, credential_ref: credentialRef || null, features: runtime === "codex-cli" ? ["cwd-binding", "workspace-write"] : ["role-turn"] }) }),
    onSuccess: async () => { setName(""); setCredentialRef(""); await client.invalidateQueries({ queryKey: ["agents"] }); },
  });
  const health = useMutation({ mutationFn: (id: string) => request<Instance>(`/v1/agent-instances/${id}/health-check`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });
  const toggle = useMutation({ mutationFn: (instance: Instance) => request<Instance>(`/v1/agent-instances/${instance.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: instance.version, enabled: !instance.enabled }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });

  if (instances.isLoading) return <LoadingState label="正在读取智能体实例注册表…"/>;
  if (instances.error) return <ErrorState error={instances.error} retry={() => instances.refetch()}/>;
  return <div className="agents-layout">
    <section className="panel"><div className="panel-head"><span>运行实例</span><small>{instances.data?.length ?? 0} 个已注册</small></div><div className="instance-grid">{instances.data?.map((item) => <article key={item.id}><div className="instance-icon"><Bot size={20}/></div><div className="instance-main"><div><span className="eyebrow">{runtimeTypeLabel(item.runtime_type)}</span><h3>{item.name}</h3></div><StatusBadge value={item.enabled ? item.health.status : "cancelled"}/><dl><dt>运行身份</dt><dd>{identityLabel(item.health.identity ?? undefined)}</dd><dt>能力特征</dt><dd>{item.features.join(" · ") || "未声明"}</dd><dt>凭据</dt><dd>{item.credential_ref || "不需要凭据引用"}</dd></dl><div className="row-actions"><button onClick={() => health.mutate(item.id)}><Activity size={14}/>健康检查</button><button onClick={() => toggle.mutate(item)}>{item.enabled ? "禁用新运行" : "重新启用"}</button></div></div></article>)}</div>{(health.error || toggle.error) && <ErrorState error={(health.error || toggle.error)!}/>}</section>
    <section className="panel compact-form"><div className="panel-head"><span>注册实例</span><small>只保存凭据引用</small></div><label>实例名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Codex 后端执行器 01"/></label><label>运行时类型<select value={runtime} onChange={(event) => setRuntime(event.target.value as RuntimeType)}><option value="codex-cli">Codex 命令行</option><option value="hermes-http">Hermes HTTP 服务</option><option value="hermes-acp">Hermes ACP 服务</option></select></label><label>凭据引用<input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} placeholder="env:HERMES_API_KEY 或 keychain:名称"/></label><p className="field-help">密钥值不会进入 API、日志或 SQLite。仅接受环境变量或系统钥匙串引用。</p><button className="primary button-icon" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}><Plus size={16}/>注册实例</button>{create.error && <ErrorState error={create.error}/>}</section>
  </div>;
}

