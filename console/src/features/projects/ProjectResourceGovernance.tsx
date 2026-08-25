import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, GitBranch, LockKeyhole } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useIdentity } from "../identity/AuthGate";
import { projectKeys, type ProjectDetail } from "./api";

type Pipeline = components["schemas"]["Pipeline"];
type AgentDeployment = components["schemas"]["AgentDeployment"];
type ProjectPipelineBinding = components["schemas"]["ProjectPipelineBinding"];
type ProjectDeploymentAccess = components["schemas"]["ProjectDeploymentAccess"];
type ProjectBindingUpdate = components["schemas"]["ProjectBindingUpdate"];
type ProjectDeploymentUpdate = components["schemas"]["ProjectDeploymentUpdate"];

export function ProjectResourceGovernance({ detail }: { detail: ProjectDetail }) {
  const { user } = useIdentity();
  const projectId = detail.project.id;
  const client = useQueryClient();
  const pipelines = useQuery({ queryKey: ["pipelines", "project-governance"], queryFn: ({ signal }) => request<Pipeline[]>("/v1/pipelines", { signal }) });
  const deployments = useQuery({ queryKey: ["agent-deployments", "project-governance"], queryFn: ({ signal }) => request<AgentDeployment[]>("/v1/agent-deployments", { signal }) });
  const refresh = async () => client.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
  const updatePipeline = useMutation({
    mutationFn: (payload: ProjectBindingUpdate) => request<ProjectPipelineBinding>(`/v1/projects/${encodeURIComponent(projectId)}/pipeline-bindings`, { method: "PUT", body: JSON.stringify(payload) }),
    onSuccess: refresh,
  });
  const updateDeployment = useMutation({
    mutationFn: (payload: ProjectDeploymentUpdate) => request<ProjectDeploymentAccess>(`/v1/projects/${encodeURIComponent(projectId)}/deployment-access`, { method: "PUT", body: JSON.stringify(payload) }),
    onSuccess: refresh,
  });
  const mutable = detail.project.lifecycle_status === "active" && user.role === "administrator";
  const defaultBinding = detail.pipeline_bindings.find((binding) => binding.enabled && binding.is_default);

  if (pipelines.isLoading || deployments.isLoading) return <LoadingState label="正在读取项目可复用资源…"/>;
  if (pipelines.error) return <ErrorState error={pipelines.error} retry={() => pipelines.refetch()}/>;
  if (deployments.error) return <ErrorState error={deployments.error} retry={() => deployments.refetch()}/>;

  return <section className="panel project-resource-governance">
    <div className="panel-head"><span>项目资源授权</span><small><LockKeyhole size={13}/>只影响之后创建的交付</small></div>
    {!mutable && <div className="policy-note">{detail.project.lifecycle_status === "archived" ? "项目已归档，资源授权保持只读。" : "只有系统管理员可以修改项目资源授权。"}</div>}
    <div className="project-resource-grid">
      <article>
        <header><GitBranch size={18}/><div><h3>流水线版本</h3><p>每个项目只能有一个默认版本；已有交付继续使用启动时冻结的 Revision。</p></div></header>
        {(pipelines.data ?? []).filter((pipeline) => pipeline.active_revision !== null).length === 0 ? <EmptyState title="没有可授权流水线" detail="请先在可视化编排中发布并激活版本。"/> : <div className="resource-access-list">{(pipelines.data ?? []).filter((pipeline) => pipeline.active_revision !== null).map((pipeline) => {
          const revision = pipeline.active_revision!;
          const binding = detail.pipeline_bindings.find((item) => item.pipeline_id === pipeline.id && item.pipeline_revision === revision);
          const enabled = Boolean(binding?.enabled);
          return <div key={`${pipeline.id}:${revision}`}><div><b>{pipeline.name}</b><small><code>{pipeline.id}:R{revision}</code></small></div><StatusBadge value={binding?.is_default && enabled ? "default" : enabled ? "enabled" : "disabled"}/><div className="resource-actions">{!enabled ? <button className="secondary" disabled={!mutable || updatePipeline.isPending} onClick={() => updatePipeline.mutate({ pipeline_revision_id: `${pipeline.id}:${revision}`, enabled: true, is_default: !defaultBinding, expected_version: binding?.version })}>授权</button> : <><button className="secondary" disabled={!mutable || binding?.is_default || updatePipeline.isPending} onClick={() => updatePipeline.mutate({ pipeline_revision_id: `${pipeline.id}:${revision}`, enabled: true, is_default: true, expected_version: binding?.version })}>设为默认</button><button className="danger" title={binding?.is_default ? "请先将另一条流水线设为默认" : undefined} disabled={!mutable || binding?.is_default || updatePipeline.isPending} onClick={() => updatePipeline.mutate({ pipeline_revision_id: `${pipeline.id}:${revision}`, enabled: false, is_default: false, expected_version: binding?.version })}>停用</button></>}</div></div>;
        })}</div>}
        {updatePipeline.error && <ErrorState error={updatePipeline.error}/>}
      </article>
      <article>
        <header><Bot size={18}/><div><h3>Agent 部署访问</h3><p>只有已启用且资格通过的 Deployment 才能加入新的项目执行快照。</p></div></header>
        {(deployments.data ?? []).length === 0 ? <EmptyState title="没有 Agent 部署" detail="请先在智能体实例中发布角色并完成部署资格检查。"/> : <div className="resource-access-list">{(deployments.data ?? []).map((deployment) => {
          const access = detail.deployment_access.find((item) => item.deployment_id === deployment.id);
          const enabled = Boolean(access?.enabled);
          const usable = deployment.enabled && deployment.qualification_status === "qualified";
          return <div key={deployment.id}><div><b>{deployment.name}</b><small>{deployment.profile_id} · {deployment.adapter_id}</small></div><StatusBadge value={enabled ? "enabled" : usable ? "qualified" : deployment.qualification_status}/><div className="resource-actions"><button className={enabled ? "danger" : "secondary"} disabled={!mutable || updateDeployment.isPending || (!enabled && !usable)} title={!enabled && !usable ? "部署必须先启用并通过资格检查" : undefined} onClick={() => updateDeployment.mutate({ deployment_id: deployment.id, enabled: !enabled, expected_version: access?.version })}>{enabled ? "撤销授权" : "授权使用"}</button></div></div>;
        })}</div>}
        {updateDeployment.error && <ErrorState error={updateDeployment.error}/>}
      </article>
    </div>
  </section>;
}
