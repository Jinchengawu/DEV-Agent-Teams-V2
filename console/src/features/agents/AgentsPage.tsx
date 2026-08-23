import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Link2, Plus } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { identityLabel, runtimeTypeLabel } from "../../i18n";

type Instance = components["schemas"]["AgentInstance"];
type RuntimeType = Instance["runtime_type"];
type Binding = components["schemas"]["CapabilityBinding"];

const capabilities = [
  { id: "hermes-pm", label: "需求分析", detail: "PM 角色结构化需求产物" },
  { id: "hermes-project-admin", label: "任务规划", detail: "Project Admin 单任务合同" },
  { id: "codex-backend", label: "后端代码交付", detail: "隔离工作区中的受控代码执行" },
] as const;

export function AgentsPage() {
  const client = useQueryClient();
  const instances = useQuery({ queryKey: ["agents"], queryFn: () => request<Instance[]>("/v1/agent-instances") });
  const bindings = useQuery({ queryKey: ["capability-bindings"], queryFn: () => request<Binding[]>("/v1/capability-bindings") });
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<RuntimeType>("codex-cli");
  const [credentialRef, setCredentialRef] = useState("");
  const create = useMutation({
    mutationFn: () => request<Instance>("/v1/agent-instances", { method: "POST", body: JSON.stringify({ name, runtime_type: runtime, connection: runtime === "codex-cli" ? { command: "codex" } : { endpoint: "http://127.0.0.1:9000" }, credential_ref: credentialRef || null, features: runtime === "codex-cli" ? ["cwd-binding", "workspace-write", "structured-output"] : ["role-turn", "structured-output"] }) }),
    onSuccess: async () => { setName(""); setCredentialRef(""); await client.invalidateQueries({ queryKey: ["agents"] }); },
  });
  const health = useMutation({ mutationFn: (id: string) => request<Instance>(`/v1/agent-instances/${id}/health-check`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });
  const toggle = useMutation({ mutationFn: (instance: Instance) => request<Instance>(`/v1/agent-instances/${instance.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: instance.version, enabled: !instance.enabled }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }) });
  const bind = useMutation({
    mutationFn: ({ capabilityId, instanceId, expectedVersion }: { capabilityId: string; instanceId: string; expectedVersion: number }) => request<Binding>(`/v1/capability-bindings/${capabilityId}`, { method: "PUT", body: JSON.stringify({ instance_id: instanceId, expected_version: expectedVersion }) }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ["capability-bindings"] }); },
  });

  if (instances.isLoading || bindings.isLoading) return <LoadingState label="正在读取智能体实例与能力绑定…"/>;
  if (instances.error || bindings.error) return <ErrorState error={(instances.error || bindings.error)!} retry={() => { void instances.refetch(); void bindings.refetch(); }}/>;
  const registered = instances.data ?? [];
  const currentBindings = bindings.data ?? [];

  return <div className="agents-layout">
    <section className="panel"><div className="panel-head"><span>运行实例</span><small>{registered.length} 个已注册</small></div><div className="instance-grid">{registered.map((item) => {
      const currentHealth = item.health ?? { status: "unknown" as const };
      return <article key={item.id}><div className="instance-icon"><Bot size={20}/></div><div className="instance-main"><div><span className="eyebrow">{runtimeTypeLabel(item.runtime_type)}</span><h3>{item.name}</h3></div><StatusBadge value={item.enabled ? currentHealth.status : "cancelled"}/><dl><dt>运行身份</dt><dd>{identityLabel(currentHealth.identity ?? undefined)}</dd><dt>能力特征</dt><dd>{item.features.join(" · ") || "未声明"}</dd><dt>配置版本</dt><dd>{item.version}</dd><dt>凭据</dt><dd>{item.credential_ref || "不需要凭据引用"}</dd></dl><div className="row-actions"><button onClick={() => health.mutate(item.id)}><Activity size={14}/>健康检查</button><button onClick={() => toggle.mutate(item)}>{item.enabled ? "禁用新运行" : "重新启用"}</button></div></div></article>;
    })}</div>{registered.length === 0 && <EmptyState title="尚未注册运行实例" detail="先在右侧注册实例，完成健康检查后才能绑定能力。"/>}{(health.error || toggle.error) && <ErrorState error={(health.error || toggle.error)!}/>}</section>

    <section className="panel compact-form"><div className="panel-head"><span>注册实例</span><small>只保存凭据引用</small></div><label>实例名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Codex 后端执行器 01"/></label><label>运行时类型<select value={runtime} onChange={(event) => setRuntime(event.target.value as RuntimeType)}><option value="codex-cli">Codex 命令行</option><option value="hermes-http">Hermes HTTP 服务</option><option value="hermes-acp">Hermes ACP 服务</option></select></label><label>凭据引用<input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} placeholder="env:HERMES_API_KEY 或 keychain:名称"/></label><p className="field-help">密钥值不会进入 API、日志或 SQLite。仅接受环境变量或系统钥匙串引用。</p><button className="primary button-icon" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}><Plus size={16}/>注册实例</button>{create.error && <ErrorState error={create.error}/>}</section>

    <section className="panel capability-bindings"><div className="panel-head"><span>能力绑定</span><small>发布 Journey 时冻结快照</small></div><p className="field-help">绑定变更不会篡改已发布版本。修改后必须回到“可视化编排”校验并发布新 Revision。</p>
      <div className="binding-grid">{capabilities.map((capability) => <CapabilityBindingEditor key={capability.id} capability={capability} binding={currentBindings.find((item) => item.capability_id === capability.id)} instances={registered} pending={bind.isPending} onBind={(instanceId, expectedVersion) => bind.mutate({ capabilityId: capability.id, instanceId, expectedVersion })}/>)}</div>
      {bind.error && <ErrorState error={bind.error}/>}
    </section>
  </div>;
}

function CapabilityBindingEditor({ capability, binding, instances, pending, onBind }: { capability: typeof capabilities[number]; binding?: Binding; instances: Instance[]; pending: boolean; onBind: (instanceId: string, expectedVersion: number) => void }) {
  const eligible = instances.filter((instance) => isCompatible(capability.id, instance));
  const [selectedId, setSelectedId] = useState(binding?.instance_id ?? "");
  const current = instances.find((instance) => instance.id === binding?.instance_id);
  return <article className="binding-card"><div><span className="eyebrow">{capability.id}</span><h3>{capability.label}</h3><p>{capability.detail}</p></div><dl><dt>当前实例</dt><dd>{current?.name ?? "未绑定"}</dd><dt>快照版本</dt><dd>{binding?.instance_version ?? "未生成"}</dd><dt>运行身份</dt><dd>{identityLabel(current?.health?.identity ?? undefined)}</dd></dl><label>选择已就绪实例<select aria-label={`为 ${capability.label} 选择实例`} value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="">请选择</option>{eligible.map((instance) => <option key={instance.id} value={instance.id}>{instance.name} · {identityLabel(instance.health?.identity ?? undefined)}</option>)}</select></label><button className="secondary button-icon" disabled={pending || !selectedId || selectedId === binding?.instance_id} onClick={() => onBind(selectedId, binding?.version ?? 0)}><Link2 size={15}/>保存能力绑定</button>{eligible.length === 0 && <small className="field-help">没有已启用、健康且能力特征匹配的实例。</small>}</article>;
}

function isCompatible(capabilityId: string, instance: Instance) {
  if (!instance.enabled || instance.health?.status !== "ready") return false;
  if (capabilityId === "codex-backend") return instance.runtime_type === "codex-cli" && instance.features.includes("cwd-binding");
  return instance.features.includes("structured-output") || instance.features.includes("text-final");
}
