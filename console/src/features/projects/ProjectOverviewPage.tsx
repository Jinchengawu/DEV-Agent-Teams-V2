import { Database, FileCheck2, GitBranch, LayoutDashboard } from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { projectPath, useArchiveProject, useProject, useProjectId, useResetProjectWorkspace, useRetryProjectWorkspace } from "./api";

const sections = [
  { id: "deliveries", label: "交付中心", note: "创建、审批并应用真实 Git Candidate", icon: GitBranch },
  { id: "board", label: "项目看板", note: "只投影当前项目的任务和合法命令", icon: LayoutDashboard },
  { id: "knowledge", label: "知识中心", note: "项目 Wiki、全局 Wiki 与不可变证据统一检索", icon: Database },
  { id: "evidence", label: "证据账本", note: "按项目隔离查询，不可修改归属", icon: FileCheck2 },
] as const;

export function ProjectOverviewPage() {
  const projectId = useProjectId();
  const detail = useProject(projectId);
  if (detail.isLoading) return <LoadingState label="正在读取项目执行上下文…"/>;
  if (detail.error) return <ErrorState error={detail.error} retry={() => detail.refetch()}/>;
  if (!detail.data) return <EmptyState title="项目不存在" detail="返回项目目录重新选择。"/>;
  const { project, workspace } = detail.data;
  const retry = useRetryProjectWorkspace(projectId);
  const reset = useResetProjectWorkspace(projectId);
  const archive = useArchiveProject(projectId, project.version);
  const actionError = retry.error ?? reset.error ?? archive.error;
  return <div className="project-overview">
    <section className="panel project-hero"><div><span className="eyebrow">项目执行边界</span><h2>{project.name}</h2><p>{project.description || "尚未填写项目说明。"}</p></div><StatusBadge value={project.lifecycle_status}/><dl><dt>项目 ID</dt><dd><code>{project.id}</code></dd><dt>工作区状态</dt><dd><StatusBadge value={workspace.status}/></dd><dt>种子 Revision</dt><dd><code>{workspace.seed_revision ?? "尚未生成"}</code></dd><dt>活动交付租约</dt><dd>{detail.data.active_delivery_id ?? "无"}</dd></dl></section>
    <section className="project-section-grid">{sections.map(({ id, label, note, icon: Icon }) => <Link key={id} to={projectPath(projectId, id)} className="project-section-card"><Icon size={22}/><div><b>{label}</b><span>{note}</span></div></Link>)}</section>
    <section className="panel"><div className="panel-head"><span>冻结的项目治理配置</span><small>只影响之后创建的交付</small></div><div className="project-binding-summary"><div><b>流水线绑定</b>{detail.data.pipeline_bindings.map((binding) => <code key={`${binding.pipeline_id}:${binding.pipeline_revision}`}>{binding.pipeline_id}:R{binding.pipeline_revision}{binding.is_default ? " · 默认" : ""}</code>)}</div><div><b>Agent 部署授权</b>{detail.data.deployment_access.filter((access) => access.enabled).map((access) => <code key={access.deployment_id}>{access.deployment_id}</code>)}</div></div><div className="project-actions">{project.lifecycle_status === "provision_failed" && <button className="primary" disabled={retry.isPending} onClick={() => retry.mutate()}>重新初始化工作区</button>}<button className="secondary" disabled={project.lifecycle_status !== "active" || Boolean(detail.data.active_delivery_id) || reset.isPending} onClick={() => window.confirm("确认重置当前项目工作区？未应用的候选不会进入 Main。") && reset.mutate()}>重置项目工作区</button><button className="danger" disabled={project.lifecycle_status !== "active" || Boolean(detail.data.active_delivery_id) || archive.isPending} onClick={() => window.confirm("归档后本版本不支持恢复，确认继续？") && archive.mutate()}>归档项目</button></div>{reset.data && <p className="field-help">重置完成，当前 Main Revision：<code>{reset.data.main_revision}</code></p>}{actionError && <ErrorState error={actionError}/>}</section>
  </div>;
}
