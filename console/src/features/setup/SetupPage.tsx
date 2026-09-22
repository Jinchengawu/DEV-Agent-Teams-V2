import { Button, Card } from "antd";
import { ArrowRight, RefreshCw } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { LEGACY_PROJECT_ID, readActiveProjectId, useProjects } from "../../entities/project/api";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useSetupReadiness, type SetupReadinessReport } from "./api";

export function SetupPage() {
  const projects = useProjects();
  const [searchParams] = useSearchParams();
  const remembered = readActiveProjectId();
  const requested = searchParams.get("project_id") ?? undefined;
  const projectId = projects.data?.some((project) => project.id === requested)
    ? requested
    : projects.data?.some((project) => project.id === remembered)
      ? remembered
      : projects.data?.find((project) => project.id !== LEGACY_PROJECT_ID)?.id;
  const readiness = useSetupReadiness(projectId, !projects.isPending);

  if (projects.isPending || readiness.isPending) return <LoadingState label="正在聚合 Runtime 与项目准备度…"/>;
  if (projects.error) return <ErrorState error={projects.error} retry={() => projects.refetch()}/>;
  if (readiness.error) return <ErrorState error={readiness.error} retry={() => readiness.refetch()}/>;
  if (!readiness.data) return <EmptyState title="准备度尚不可读" detail="系统不会在未知状态下允许启动交付。"/>;

  return <div className="setup-center">
    <section className="page-heading">
      <p className="eyebrow">准备中心 · {projectId ?? "全局"}</p>
      <h2>{readiness.data.status === "ready" ? "可以进入交付评测。" : "先处理阻塞，再启动真实交付。"}</h2>
      <p>这里只投影 Runtime、Workspace、Pipeline、Deployment 和 Verification 的权威事实，不在界面伪造通过结论。</p>
      <Button icon={<RefreshCw size={15}/>} onClick={() => readiness.refetch()}>刷新检查</Button>
    </section>
    <ReadinessGroup title="Runtime" checks={readiness.data.global_checks}/>
    <ReadinessGroup title="Project Workspace 与 Execution Contract" checks={readiness.data.project_checks}/>
    {readiness.data.optional_checks.length > 0 && <ReadinessGroup title="Optional Knowledge" checks={readiness.data.optional_checks}/>}
    {!projectId && <Card className="atos-card"><EmptyState title="尚无可评测项目" detail="先创建 guided evaluation 项目，再回到准备中心。"/><Link className="secondary" to="/projects">创建评测项目</Link></Card>}
  </div>;
}

function ReadinessGroup({ title, checks }: { title: string; checks: SetupReadinessReport["global_checks"] }) {
  return <Card className="atos-card readiness-group" title={title}>
    <div className="readiness-checks">{checks.map((check) => <article key={check.id} id={check.id} className="readiness-check">
      <StatusBadge value={check.status}/>
      <div><b>{check.summary}</b><small>{check.id} · {check.blocking_scope}</small><p>{check.repair}</p></div>
      <Link to={check.navigation_target} aria-label={`打开 ${check.summary} 修复入口`}><ArrowRight size={17}/></Link>
    </article>)}</div>
  </Card>;
}
