import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FolderGit2, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { projectPath, useCreateProject, useProjects } from "./api";

type Pipeline = components["schemas"]["Pipeline"];
type Deployment = components["schemas"]["AgentDeployment"];

export function ProjectsPage() {
  const navigate = useNavigate();
  const projects = useProjects();
  const pipelines = useQuery({ queryKey: ["pipelines", "project-create"], queryFn: () => request<Pipeline[]>("/v1/pipelines") });
  const deployments = useQuery({ queryKey: ["agent-deployments", "project-create"], queryFn: () => request<Deployment[]>("/v1/agent-deployments") });
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const activePipelines = useMemo(() => (pipelines.data ?? []).filter((pipeline) => pipeline.active_revision !== null), [pipelines.data]);
  const usableDeployments = useMemo(() => (deployments.data ?? []).filter((deployment) => deployment.enabled && deployment.qualification_status === "qualified"), [deployments.data]);
  const [pipelineRevisionId, setPipelineRevisionId] = useState("");
  const [deploymentIds, setDeploymentIds] = useState<string[]>([]);
  const create = useCreateProject((projectId) => navigate(projectPath(projectId)));

  if (projects.isLoading) return <LoadingState label="正在读取项目治理目录…"/>;
  if (projects.error) return <ErrorState error={projects.error} retry={() => projects.refetch()}/>;

  return <div className="project-workbench">
    <section className="panel project-catalog">
      <div className="panel-head"><span>项目目录</span><small>{projects.data?.length ?? 0} 个项目 · 每个项目独立 Git 主分支</small></div>
      <div className="project-grid">{projects.data?.length ? projects.data.map((project) => <Link className="project-card" key={project.id} to={projectPath(project.id)}>
        <div><FolderGit2 size={20}/><StatusBadge value={project.lifecycle_status}/></div>
        <h2>{project.name}</h2><p>{project.description || "尚未填写项目说明。"}</p>
        <small>项目 ID：{project.id} · v{project.version}</small>
      </Link>) : <EmptyState title="还没有项目" detail="创建项目后，系统会为它初始化独立的 Bare Git 仓库并固定默认流水线。"/>}</div>
    </section>
    <section className="panel project-create">
      <div className="panel-head"><span>创建项目</span><small>初始化失败会保留可重试记录，不回退共享沙箱</small></div>
      <label>项目标识<input value={id} onChange={(event) => setId(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} placeholder="例如：pj1"/></label>
      <label>项目名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：客户门户后端"/></label>
      <label>项目说明<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明项目边界和验收目标"/></label>
      <label>默认流水线<select value={pipelineRevisionId} onChange={(event) => setPipelineRevisionId(event.target.value)}><option value="">请选择已激活的固定版本</option>{activePipelines.map((pipeline) => <option key={pipeline.id} value={`${pipeline.id}:${pipeline.active_revision}`}>{pipeline.name} · R{pipeline.active_revision}</option>)}</select></label>
      <fieldset><legend>允许的 Agent 部署</legend>{usableDeployments.length ? usableDeployments.map((deployment) => <label className="check-row" key={deployment.id}><input type="checkbox" checked={deploymentIds.includes(deployment.id)} onChange={(event) => setDeploymentIds((current) => event.target.checked ? [...current, deployment.id] : current.filter((value) => value !== deployment.id))}/><span>{deployment.name}<small>{deployment.id}</small></span></label>) : <p className="field-warning">没有已启用且资格通过的部署。请先在“智能体实例”中完成部署。</p>}</fieldset>
      <button className="primary" disabled={create.isPending || !id || !name.trim() || !pipelineRevisionId} onClick={() => create.mutate({ id, name: name.trim(), description: description.trim(), default_pipeline_revision_id: pipelineRevisionId, deployment_ids: deploymentIds })}><Plus size={16}/>{create.isPending ? "正在初始化项目…" : "创建并初始化独立工作区"}</button>
      {create.error && <ErrorState error={create.error}/>} 
    </section>
  </div>;
}
