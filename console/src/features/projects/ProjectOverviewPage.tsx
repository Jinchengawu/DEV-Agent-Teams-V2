import { useState } from "react";
import { Button } from "antd";
import { Database, FileCheck2, GitBranch, LayoutDashboard } from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { projectPath, useArchiveProject, useProject, useProjectId, useProvisionFullstackRepositories, useResetProjectWorkspace, useRetryProjectWorkspace, type ProjectDetail } from "./api";
import { ProjectResourceGovernance } from "./ProjectResourceGovernance";
import { ProjectKnowledgeGovernance } from "./ProjectKnowledgeGovernance";
import { ProjectWorkcellSetup } from "../workcells/ProjectWorkcellSetup";

const sections = [
  { id: "deliveries", label: "交付中心", note: "创建、审批并应用真实 Git Candidate", icon: GitBranch },
  { id: "board", label: "项目看板", note: "只投影当前项目的任务和合法命令", icon: LayoutDashboard },
  { id: "knowledge", label: "知识中心", note: "项目 Wiki、全局 Wiki 与不可变证据统一检索", icon: Database },
  { id: "evidence", label: "证据账本", note: "按项目隔离查询，不可修改归属", icon: FileCheck2 },
] as const;
const fullstackRepositoryRoles = ["backend", "design", "frontend", "qa"] as const;

export function ProjectOverviewPage() {
  const projectId = useProjectId();
  const detail = useProject(projectId);
  if (detail.isLoading) return <LoadingState label="正在读取项目执行上下文…"/>;
  if (detail.error) return <ErrorState error={detail.error} retry={() => detail.refetch()}/>;
  if (!detail.data) return <EmptyState title="项目不存在" detail="返回项目目录重新选择。"/>;
  return <ProjectOverviewReady detail={detail.data}/>;
}

function ProjectOverviewReady({ detail }: { detail: ProjectDetail }) {
  const { project, workspace } = detail;
  const retry = useRetryProjectWorkspace(project.id);
  const reset = useResetProjectWorkspace(project.id);
  const archive = useArchiveProject(project.id, project.version);
  const provisionFullstack = useProvisionFullstackRepositories(project.id);
  const [confirmation, setConfirmation] = useState<"reset" | "archive">();
  const actionError = retry.error ?? reset.error ?? archive.error ?? provisionFullstack.error;
  const repositories = detail.repositories ?? [];
  const approvedKnowledgeSources = detail.knowledge_source_approvals ?? [];
  const repositoryRoles = new Set(repositories.map((repository) => repository.role));
  const fullstackReady = fullstackRepositoryRoles.every((role) => repositoryRoles.has(role) && repositories.find((repository) => repository.role === role)?.status === "ready");

  return <div className="project-overview">
    <section className="panel project-hero"><div><span className="eyebrow">项目执行边界</span><h2>{project.name}</h2><p>{project.description || "尚未填写项目说明。"}</p></div><StatusBadge value={project.lifecycle_status}/><dl><dt>项目 ID</dt><dd><code>{project.id}</code></dd><dt>工作区状态</dt><dd><StatusBadge value={workspace.status}/></dd><dt>种子 Revision</dt><dd><code>{workspace.seed_revision ?? "尚未生成"}</code></dd><dt>活动交付租约</dt><dd>{detail.active_delivery_id ?? "无"}</dd></dl></section>
    <section className="project-section-grid">{sections.map(({ id, label, note, icon: Icon }) => <Link key={id} to={projectPath(project.id, id)} className="project-section-card"><Icon size={22}/><div><b>{label}</b><span>{note}</span></div></Link>)}</section>
    <section className="panel"><div className="panel-head"><span>当前项目治理配置</span><small>运行时会冻结到每个新交付的执行快照</small></div><div className="project-binding-summary"><div><b>流水线绑定</b>{detail.pipeline_bindings.length ? detail.pipeline_bindings.map((binding) => <code key={`${binding.pipeline_id}:${binding.pipeline_revision}`}>{binding.pipeline_id}:R{binding.pipeline_revision}{binding.is_default ? " · 默认" : ""}</code>) : <span>未绑定</span>}</div><div><b>Agent 部署授权</b>{detail.deployment_access.some((access) => access.enabled) ? detail.deployment_access.filter((access) => access.enabled).map((access) => <code key={access.deployment_id}>{access.deployment_id}</code>) : <span>未授权</span>}</div><div><b>项目仓库集合</b>{repositories.length ? repositories.map((repository) => <code key={repository.role}>{repository.role} · {repository.status}{repository.seed_revision ? ` · ${repository.seed_revision.slice(0, 8)}` : ""}</code>) : <span>尚未初始化</span>}</div><div><b>已批准知识来源</b>{approvedKnowledgeSources.some((source) => source.enabled) ? approvedKnowledgeSources.filter((source) => source.enabled).map((source) => <code key={source.binding_id}>{source.binding_id} · {source.rag_enabled ? "RAG enabled" : "sync only"}</code>) : <span>未批准（项目 Wiki 与交付证据仍可检索）</span>}</div></div><div className="project-actions">{project.lifecycle_status === "provision_failed" && <Button type="primary" disabled={retry.isPending} onClick={() => retry.mutate()}>重新初始化工作区</Button>}{!fullstackReady && <Button type="primary" disabled={project.lifecycle_status !== "active" || Boolean(detail.active_delivery_id) || provisionFullstack.isPending} loading={provisionFullstack.isPending} onClick={() => provisionFullstack.mutate()}>初始化前端、设计与测试仓库</Button>}<Button disabled={project.lifecycle_status !== "active" || Boolean(detail.active_delivery_id) || reset.isPending} onClick={() => setConfirmation("reset")}>重置项目工作区</Button><Button danger disabled={project.lifecycle_status !== "active" || Boolean(detail.active_delivery_id) || archive.isPending} onClick={() => setConfirmation("archive")}>归档项目</Button></div>{reset.data && <p className="field-help">重置完成，当前 Main Revision：<code>{reset.data.main_revision}</code></p>}{actionError && <ErrorState error={actionError}/>}</section>
    <ProjectResourceGovernance detail={detail}/>
    <ProjectKnowledgeGovernance detail={detail}/>
    {workspace.repository_ref.startsWith("workspace-set/")
      ? <ProjectWorkcellSetup projectId={project.id}/>
      : null}
    <ConfirmDialog open={confirmation === "reset"} title={`重置“${project.name}”工作区`} detail="Main 将恢复为项目种子版本，未应用候选不会进入 Main。该操作只影响当前项目，执行前请确认没有仍需保留的候选工作区。" confirmLabel="确认重置工作区" tone="danger" pending={reset.isPending} onCancel={() => setConfirmation(undefined)} onConfirm={() => reset.mutate(undefined, { onSuccess: () => setConfirmation(undefined) })}/>
    <ConfirmDialog open={confirmation === "archive"} title={`归档“${project.name}”`} detail="归档后将禁止创建交付、修改资源绑定和重置工作区；当前版本不支持恢复，历史交付和证据仍可查询。" confirmLabel="确认归档项目" tone="danger" pending={archive.isPending} onCancel={() => setConfirmation(undefined)} onConfirm={() => archive.mutate(undefined, { onSuccess: () => setConfirmation(undefined) })}/>
  </div>;
}
