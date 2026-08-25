import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Boxes, Plus, ServerCog } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { identityLabel, runtimeTypeLabel } from "../../i18n";
import { AgentProfilesPanel } from "./AgentProfilesPanel";
import { AgentDeploymentsPanel } from "./AgentDeploymentsPanel";

type Instance = components["schemas"]["AgentInstance"];
type RuntimeType = Instance["runtime_type"];
type Provider = components["schemas"]["ProviderManifestView"];
type Adapter = components["schemas"]["RuntimeAdapterDescriptor"];
type AgentWorkspace = "profiles" | "deployments" | "instances" | "providers" | "adapters";
export function AgentsPage() {
  const client = useQueryClient();
  const instances = useQuery({
    queryKey: ["agents"],
    queryFn: () => request<Instance[]>("/v1/agent-instances"),
  });
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<RuntimeType>("codex-cli");
  const [connectionTarget, setConnectionTarget] = useState("codex");
  const [credentialRef, setCredentialRef] = useState("");
  const [pendingDisable, setPendingDisable] = useState<Instance>();
  const [workspace, setWorkspace] = useState<AgentWorkspace>("profiles");
  const create = useMutation({
    mutationFn: () =>
      request<Instance>("/v1/agent-instances", {
        method: "POST",
        body: JSON.stringify({
          name,
          runtime_type: runtime,
          connection:
            runtime === "codex-cli"
              ? { command: connectionTarget }
              : { endpoint: connectionTarget },
          credential_ref: credentialRef || null,
        }),
      }),
    onSuccess: async () => {
      setName("");
      setCredentialRef("");
      await client.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const health = useMutation({
    mutationFn: (id: string) =>
      request<Instance>(`/v1/agent-instances/${id}/health-check`, {
        method: "POST",
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }),
  });
  const toggle = useMutation({
    mutationFn: (instance: Instance) =>
      request<Instance>(`/v1/agent-instances/${instance.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: instance.version,
          enabled: !instance.enabled,
        }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agents"] }),
  });
  const registered = instances.data ?? [];

  return (
    <div className="agent-management">
      <nav className="agent-workspace-tabs" role="tablist" aria-label="Agent 管理工作区">
        <WorkspaceTab id="profiles" label="Agent 角色" current={workspace} onSelect={setWorkspace}/>
        <WorkspaceTab id="deployments" label="Agent 部署" current={workspace} onSelect={setWorkspace}/>
        <WorkspaceTab id="instances" label="运行实例" current={workspace} onSelect={setWorkspace}/>
        <WorkspaceTab id="providers" label="Provider 能力" current={workspace} onSelect={setWorkspace}/>
        <WorkspaceTab id="adapters" label="Runtime Adapter" current={workspace} onSelect={setWorkspace}/>
      </nav>
      <div className="agent-workspace" role="tabpanel">
      {workspace === "profiles" && <AgentProfilesPanel />}
      {workspace === "deployments" && <AgentDeploymentsPanel />}
      {workspace === "instances" && (instances.isLoading ? <LoadingState label="正在读取智能体运行实例…"/> : instances.error ? <ErrorState error={instances.error} retry={() => { void instances.refetch(); }}/> : <div className="agents-layout runtime-instance-workspace"><section className="panel">
        <div className="panel-head">
          <span>运行实例</span>
          <small>{registered.length} 个已注册</small>
        </div>
        <div className="instance-grid">
          {registered.map((item) => {
            const currentHealth = item.health ?? { status: "unknown" as const };
            return (
              <article key={item.id}>
                <div className="instance-icon">
                  <Bot size={20} />
                </div>
                <div className="instance-main">
                  <div>
                    <span className="eyebrow">
                      {runtimeTypeLabel(item.runtime_type)}
                    </span>
                    <h3>{item.name}</h3>
                  </div>
                  <StatusBadge
                    value={item.enabled ? currentHealth.status : "cancelled"}
                  />
                  <dl>
                    <dt>运行身份</dt>
                    <dd>
                      {identityLabel(currentHealth.identity ?? undefined)}
                    </dd>
                    <dt>连接配置</dt>
                    <dd>{connectionLabel(item)}</dd>
                    <dt>能力特征</dt>
                    <dd>{item.features.join(" · ") || "未声明"}</dd>
                    <dt>配置版本</dt>
                    <dd>{item.version}</dd>
                    <dt>凭据</dt>
                    <dd>{item.credential_ref || "不需要凭据引用"}</dd>
                  </dl>
                  <div className="row-actions">
                    <button onClick={() => health.mutate(item.id)}>
                      <Activity size={14} />
                      健康检查
                    </button>
                    <button
                      onClick={() =>
                        item.enabled
                          ? setPendingDisable(item)
                          : toggle.mutate(item)
                      }
                    >
                      {item.enabled ? "禁用新运行" : "重新启用"}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
        {registered.length === 0 && (
          <EmptyState
            title="尚未注册运行实例"
            detail="先在右侧注册实例，完成健康检查后才能绑定能力。"
          />
        )}
        {(health.error || toggle.error) && (
          <ErrorState error={(health.error || toggle.error)!} />
        )}
      </section>

      <section className="panel compact-form">
        <div className="panel-head">
          <span>注册实例</span>
          <small>只保存凭据引用</small>
        </div>
        <label>
          实例名称
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：Codex 后端执行器 01"
          />
        </label>
        <label>
          运行时类型
          <select
            value={runtime}
            onChange={(event) => {
              const next = event.target.value as RuntimeType;
              setRuntime(next);
              setConnectionTarget(next === "codex-cli" ? "codex" : "");
              create.reset();
            }}
          >
            <option value="codex-cli">Codex 命令行</option>
            <option value="hermes-http">Hermes HTTP 服务</option>
            <option value="hermes-acp">Hermes ACP 服务</option>
          </select>
        </label>
        <label>
          {runtime === "codex-cli" ? "命令" : "连接端点"}
          <input
            value={connectionTarget}
            onChange={(event) => setConnectionTarget(event.target.value)}
            placeholder={
              runtime === "codex-cli"
                ? "例如：codex"
                : "例如：http://127.0.0.1:9000"
            }
          />
        </label>
        <label>
          凭据引用
          <input
            value={credentialRef}
            onChange={(event) => setCredentialRef(event.target.value)}
            placeholder="env:HERMES_API_KEY 或 keychain:名称"
          />
        </label>
        <p className="field-help">
          密钥值不会进入 API、日志或 SQLite。仅接受环境变量或系统钥匙串引用。
        </p>
        <button
          className="primary button-icon"
          disabled={
            !name.trim() || !connectionTarget.trim() || create.isPending
          }
          onClick={() => create.mutate()}
        >
          <Plus size={16} />
          注册实例
        </button>
        {create.error && <ErrorState error={create.error} />}
      </section></div>)}
      {workspace === "providers" && <ProviderCatalog/>}
      {workspace === "adapters" && <RuntimeAdapterCatalog/>}
      </div>

      <ConfirmDialog
        open={Boolean(pendingDisable)}
        title={`禁用运行实例“${pendingDisable?.name ?? ""}”`}
        detail="禁用后，该实例不能承接新的 Agent Run；已发布流水线仍保留原运行快照，但新交付会因实例不可用而被阻止。"
        confirmLabel="确认禁用新运行"
        tone="danger"
        pending={toggle.isPending}
        onCancel={() => setPendingDisable(undefined)}
        onConfirm={() => {
          if (pendingDisable)
            toggle.mutate(pendingDisable, {
              onSuccess: () => setPendingDisable(undefined),
            });
        }}
      />
    </div>
  );
}

function WorkspaceTab({ id, label, current, onSelect }: { id: AgentWorkspace; label: string; current: AgentWorkspace; onSelect: (workspace: AgentWorkspace) => void }) {
  return <button role="tab" aria-selected={current === id} className={current === id ? "active" : ""} onClick={() => onSelect(id)}>{label}</button>;
}

function ProviderCatalog() {
  const providers = useQuery({ queryKey: ["provider-manifests"], queryFn: ({ signal }) => request<Provider[]>("/v1/provider-manifests", { signal }) });
  if (providers.isLoading) return <LoadingState label="正在读取 ACWM Provider Manifest…"/>;
  if (providers.error) return <ErrorState error={providers.error} retry={() => providers.refetch()}/>;
  return <section className="panel catalog-workspace"><div className="panel-head"><span>Provider 能力目录</span><small>ACWM 语义权威 · 只读已安装 Manifest</small></div><div className="catalog-grid">{providers.data?.map((provider) => <article key={provider.id}><Boxes size={20}/><div><span className="eyebrow">Revision {provider.revision}</span><h3>{provider.id}</h3><p>{provider.capabilities.map((capability) => `${String(capability.id)}@${String(capability.version)}`).join(" · ") || "未声明 Capability"}</p></div><dl><dt>运行类型</dt><dd>{provider.runtime_types.join("、")}</dd><dt>Workflow Mode</dt><dd>{provider.workflow_modes.join("、")}</dd><dt>必要特征</dt><dd>{provider.required_features.join("、") || "无"}</dd><dt>权限要求</dt><dd>{provider.permission_requirements.join("、") || "无"}</dd><dt>指纹</dt><dd><code>{provider.fingerprint}</code></dd></dl></article>)}{providers.data?.length === 0 && <EmptyState title="尚未安装 Provider Manifest" detail="Provider 必须先由 ACWM 安装并验证，浏览器不能伪造运行特征。"/>}</div></section>;
}

function RuntimeAdapterCatalog() {
  const adapters = useQuery({ queryKey: ["runtime-adapters"], queryFn: ({ signal }) => request<Adapter[]>("/v1/runtime-adapters", { signal }) });
  if (adapters.isLoading) return <LoadingState label="正在读取 Runtime Adapter…"/>;
  if (adapters.error) return <ErrorState error={adapters.error} retry={() => adapters.refetch()}/>;
  return <section className="panel catalog-workspace"><div className="panel-head"><span>Runtime Adapter</span><small>安装清单与探测特征 · 不接受浏览器自报</small></div><div className="catalog-grid">{adapters.data?.map((adapter) => <article key={adapter.id}><ServerCog size={20}/><div><span className="eyebrow">{runtimeTypeLabel(adapter.runtime_type)}</span><h3>{adapter.id}</h3><StatusBadge value={adapter.available ? "ready" : "failed"}/></div><dl><dt>版本</dt><dd>{adapter.version ?? "未安装"}</dd><dt>可信特征</dt><dd>{adapter.features.join(" · ") || "无"}</dd><dt>特征来源</dt><dd>{adapter.features_source}</dd><dt>错误代码</dt><dd>{adapter.error_code ?? "无"}</dd></dl></article>)}{adapters.data?.length === 0 && <EmptyState title="尚无 Runtime Adapter" detail="安装 ACWM Adapter 后才能将逻辑角色部署到真实运行实例。"/>}</div></section>;
}

function connectionLabel(instance: Instance) {
  return instance.runtime_type === "codex-cli"
    ? (instance.connection.command ?? "未配置命令")
    : (instance.connection.endpoint ?? "未配置端点");
}
