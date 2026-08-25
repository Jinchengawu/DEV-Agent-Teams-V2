import { useEffect, useState } from "react";
import { Button, Select, Tooltip } from "antd";
import { Activity, Bot, Boxes, Database, FileCheck2, FolderGit2, GitBranch, LayoutDashboard, Settings, Workflow } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useIdentity } from "../../features/identity/AuthGate";
import { LEGACY_PROJECT_ID, projectPath, readActiveProjectId, rememberActiveProjectId, useProjects, useRouteProjectId } from "../../entities/project/api";

const globalSections = [
  { path: "/projects", label: "项目", icon: FolderGit2, description: "项目治理、独立工作区与资源授权" },
  { path: "/orchestration", label: "可视化编排", icon: Workflow, description: "ACWM 线性旅程" },
  { path: "/agents", label: "智能体实例", icon: Bot, description: "实例与能力绑定" },
  { path: "/settings", label: "设置", icon: Settings, description: "安全运营参数" },
] as const;

const projectSections = [
  { section: "deliveries", label: "交付", icon: GitBranch, description: "当前项目的真实 Git 候选与双审批" },
  { section: "board", label: "看板", icon: LayoutDashboard, description: "当前项目的事件投影与合法命令" },
  { section: "knowledge", label: "知识中心", icon: Database, description: "项目知识、全局知识与来源检索" },
  { section: "evidence", label: "证据", icon: FileCheck2, description: "当前项目的不可变完整性账本" },
] as const;

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout, loggingOut } = useIdentity();
  const routeProjectId = useRouteProjectId();
  const [rememberedProjectId, setRememberedProjectId] = useState(readActiveProjectId);
  const projects = useProjects();
  const projectId = routeProjectId ?? rememberedProjectId ?? projects.data?.[0]?.id ?? LEGACY_PROJECT_ID;
  useEffect(() => {
    if (!routeProjectId) return;
    setRememberedProjectId(routeProjectId);
    rememberActiveProjectId(routeProjectId);
  }, [routeProjectId]);
  useEffect(() => {
    if (routeProjectId || !projects.data?.length || projects.data.some((project) => project.id === projectId)) return;
    const fallback = projects.data[0].id;
    setRememberedProjectId(fallback);
    rememberActiveProjectId(fallback);
  }, [projectId, projects.data, routeProjectId]);
  const scopedSection = projectSections.find((section) => location.pathname.endsWith(`/${section.section}`));
  const globalSection = globalSections.find((section) => location.pathname === section.path || location.pathname.startsWith(`${section.path}/`));
  const current = scopedSection ?? globalSection ?? globalSections[0];
  const scopedPaths = projectSections.map((section) => ({ ...section, path: projectPath(projectId, section.section) }));
  const switchProject = (nextProjectId: string) => {
    setRememberedProjectId(nextProjectId);
    rememberActiveProjectId(nextProjectId);
    navigate(projectPath(nextProjectId, scopedSection?.section ?? "overview"));
  };
  return <div className="app-shell">
    <aside className="main-sidebar">
      <div className="brand"><span className="brand-mark"><Boxes size={20}/></span><div><b>Agent-Team-OS</b><small>交付控制平面 · V0.4.0</small></div></div>
      <nav>
        <NavLink to="/projects"><FolderGit2 size={17}/><span>项目</span></NavLink>
        <div className="project-switcher"><ProjectSelect projectId={projectId} projects={projects.data} onChange={switchProject}/></div>
        {scopedPaths.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}
        <span className="nav-separator">全局目录</span>
        {globalSections.filter((section) => section.path !== "/projects").map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}
      </nav>
      <div className="system-state"><span className="pulse"/>{user.display_name}<small>{roleLabel(user.role)} · {user.username}</small><Button type="link" onClick={logout} loading={loggingOut}>退出登录</Button></div>
    </aside>
    <main>
      <header><div><p className="kicker">团队协作控制层</p><h1>{current.label}</h1><p className="page-description">{current.description}</p></div><div className="identity-chip"><Activity size={16}/><span>规划身份<br/><b>Codex 模拟 Hermes</b></span><span>执行身份<br/><b>Codex 命令行</b></span></div></header>
      <div className="mobile-project-switcher" aria-label="移动端项目切换">
        <ProjectSelect projectId={projectId} projects={projects.data} onChange={switchProject}/>
      </div>
      <Outlet key={location.pathname.startsWith("/projects/") ? projectId : "global"}/>
    </main>
  </div>;
}

function ProjectSelect({ projectId, projects, onChange }: { projectId: string; projects: Array<{ id: string; name: string }> | undefined; onChange: (projectId: string) => void }) {
  return <label>当前项目<Tooltip title={projects?.length ? "切换后，交付、看板、知识与证据会同步切换项目作用域。" : "尚无可用项目"}><Select className="shell-project-select" aria-label="当前项目" value={projectId} onChange={onChange} disabled={!projects?.length} options={projects?.map((project) => ({ value: project.id, label: project.name }))}/></Tooltip></label>;
}

function roleLabel(role: string) {
  if (role === "administrator") return "管理员";
  if (role === "editor") return "编辑者";
  return "只读者";
}
