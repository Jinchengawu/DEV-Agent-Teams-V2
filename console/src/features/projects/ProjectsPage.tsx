import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Checkbox, Form, Input, Space, Typography } from "antd";
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

  return <div className="project-workbench-v2">
    <Card className="atos-card project-catalog" title={<div className="atos-section-title"><div><h2>项目目录</h2><p>每个项目使用独立 Git 主分支和交付租约。</p></div><Typography.Text type="secondary">{projects.data?.length ?? 0} 个项目</Typography.Text></div>}>
      <div className="project-grid-v2">{projects.data?.length ? projects.data.map((project) => <Link className="project-card-v2" key={project.id} to={projectPath(project.id)}>
        <Card size="small" className="evidence-rail">
          <div className="project-card-v2-head"><FolderGit2 size={21}/><StatusBadge value={project.lifecycle_status}/></div>
          <h2>{project.name}</h2><p>{project.description || "尚未填写项目说明。"}</p>
          <small>项目 ID：{project.id} · v{project.version}</small>
        </Card>
      </Link>) : <EmptyState title="还没有项目" detail="创建项目后，系统会为它初始化独立的 Bare Git 仓库并固定默认流水线。"/>}</div>
    </Card>
    <Card className="atos-card project-create-form" title={<div className="atos-section-title"><div><h2>创建项目</h2><p>初始化失败会保留可重试记录，不回退共享沙箱。</p></div></div>}>
      <Form layout="vertical" requiredMark={false} onFinish={() => create.mutate({ id, name: name.trim(), description: description.trim(), default_pipeline_revision_id: pipelineRevisionId, deployment_ids: deploymentIds })}>
        <Form.Item label="项目标识" htmlFor="project-id" required><Input id="project-id" value={id} onChange={(event) => setId(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} placeholder="例如：pj1"/></Form.Item>
        <Form.Item label="项目名称" htmlFor="project-name" required><Input id="project-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：客户门户后端"/></Form.Item>
        <Form.Item label="项目说明" htmlFor="project-description"><Input.TextArea id="project-description" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明项目边界和验收目标"/></Form.Item>
        <Form.Item label="默认流水线" htmlFor="project-pipeline" required><select id="project-pipeline" aria-label="默认流水线" value={pipelineRevisionId} onChange={(event) => setPipelineRevisionId(event.target.value)}><option value="">请选择已激活的固定版本</option>{activePipelines.map((pipeline) => <option key={pipeline.id} value={`${pipeline.id}:${pipeline.active_revision}`}>{pipeline.name} · R{pipeline.active_revision}</option>)}</select></Form.Item>
        <Form.Item label="允许的 Agent 部署"><div className="project-deployment-list">{usableDeployments.length ? usableDeployments.map((deployment) => <label className="project-deployment-option" key={deployment.id}><Checkbox checked={deploymentIds.includes(deployment.id)} onChange={(event) => setDeploymentIds((current) => event.target.checked ? [...current, deployment.id] : current.filter((value) => value !== deployment.id))}/><span>{deployment.name}<small>{deployment.id}</small></span></label>) : <p className="field-warning">没有已启用且资格通过的部署。请先在“智能体实例”中完成部署。</p>}</div></Form.Item>
        <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Button block type="primary" htmlType="submit" icon={<Plus size={16}/>} loading={create.isPending} disabled={!id || !name.trim() || !pipelineRevisionId}>创建并初始化独立工作区</Button>
          {create.error && <ErrorState error={create.error}/>}
        </Space>
      </Form>
    </Card>
  </div>;
}
