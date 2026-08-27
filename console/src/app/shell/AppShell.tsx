import { useEffect, useState } from "react";
import { Bot, Database, FileCheck2, FolderGit2, GitBranch, LayoutDashboard, LogOut, Settings, Workflow } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import mark from "../../assets/agent-team-os-mark.svg";
import inverseMark from "../../assets/agent-team-os-mark-inverse.svg";
import { useIdentity } from "../../features/identity/AuthGate";
import { LEGACY_PROJECT_ID, projectIdFromPath, projectPath, readActiveProjectId, rememberActiveProjectId, useProjects } from "../../features/projects/api";
import { ThemeToggle } from "../../shared/ui/ThemeToggle";

const projectSections = [
  { section: "deliveries", label: "交付工作台", icon: GitBranch, description: "从目标进入当前项目的可审批交付闭环" },
  { section: "board", label: "交付看板", icon: LayoutDashboard, description: "当前项目的事件投影与合法状态命令" },
  { section: "evidence", label: "证据", icon: FileCheck2, description: "当前项目的不可变完整性账本" },
  { section: "knowledge", label: "知识中心", icon: Database, description: "当前项目与授权全局来源的可追溯内容" },
] as const;

const systemSections = [
  { path: "/projects", label: "项目", icon: FolderGit2, description: "项目治理、独立工作区与资源授权" },
  { path: "/agents", label: "智能体实例", icon: Bot, description: "角色、部署与运行实例" },
  { path: "/orchestration", label: "可视化编排", icon: Workflow, description: "Pipeline、DAG 与人工 Gate" },
  { path: "/settings", label: "设置", icon: Settings, description: "安全运营参数与发布门禁" },
] as const;

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout, loggingOut } = useIdentity();
  const routeProjectId = projectIdFromPath(location.pathname);
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

  const scopedSection = projectSections.find((section) => location.pathname.includes(`/${section.section}`));
  const globalSection = systemSections.find((section) => location.pathname === section.path || location.pathname.startsWith(`${section.path}/`));
  const current = scopedSection ?? globalSection ?? systemSections[0];
  const scopedPaths = projectSections.map((section) => ({ ...section, path: projectPath(projectId, section.section) }));
  const switchProject = (nextProjectId: string) => {
    setRememberedProjectId(nextProjectId);
    rememberActiveProjectId(nextProjectId);
    navigate(projectPath(nextProjectId, scopedSection?.section ?? "overview"));
  };

  return <div className="app-shell">
    <aside className="main-sidebar">
      <NavLink className="brand" to="/projects" aria-label="Agent-Team-OS 项目目录">
        <span className="brand-mark"><img className="brand-mark-light" src={mark} alt=""/><img className="brand-mark-dark" src={inverseMark} alt=""/></span>
        <span><b>Agent-Team-OS</b><small>交付控制平面 · V0.3.1</small></span>
      </NavLink>
      <nav aria-label="项目工作区导航"><span className="nav-label">项目工作区</span>{scopedPaths.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
      <div className="workspace-card project-switcher"><label htmlFor="active-project">当前项目</label><select id="active-project" aria-label="当前项目" value={projectId} disabled={!projects.data?.length} onChange={(event) => switchProject(event.target.value)}>{projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><small>{projects.error ? "项目目录暂不可用" : "切换后同步隔离交付、看板、知识与证据"}</small></div>
      <nav aria-label="系统目录"><span className="nav-label">系统目录</span>{systemSections.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
      <div className="system-state"><span className="identity-state"><i/>{user.display_name}</span><small>{roleLabel(user.role)} · {user.username}</small><button className="text-button" onClick={logout} disabled={loggingOut}><LogOut size={14}/>{loggingOut ? "正在退出…" : "退出登录"}</button></div>
    </aside>
    <main className="app-main">
      <header className="app-context-bar"><div className="route-context"><span>控制平面　／</span><h1>{current.label}</h1><small>{current.description}</small></div><div className="context-actions"><ThemeToggle/><div className="identity-strip"><span>规划身份<b>Codex 模拟 Hermes</b></span><span>执行身份<b>Codex CLI</b></span></div></div></header>
      <Outlet key={location.pathname.startsWith("/projects/") ? projectId : "global"}/>
    </main>
  </div>;
}

function roleLabel(role: string) {
  if (role === "administrator") return "管理员";
  if (role === "editor") return "编辑者";
  return "只读者";
}
