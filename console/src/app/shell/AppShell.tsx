import { Activity, Bot, Boxes, Database, FileCheck2, FolderGit2, GitBranch, LayoutDashboard, Settings, Workflow } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useIdentity } from "../../features/identity/AuthGate";
import { projectPath, useProjectId, useProjects } from "../../features/projects/api";

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
  const projectId = useProjectId();
  const projects = useProjects();
  const scopedSection = projectSections.find((section) => location.pathname.endsWith(`/${section.section}`));
  const globalSection = globalSections.find((section) => location.pathname === section.path || location.pathname.startsWith(`${section.path}/`));
  const current = scopedSection ?? globalSection ?? globalSections[0];
  const scopedPaths = projectSections.map((section) => ({ ...section, path: projectPath(projectId, section.section) }));
  return <div className="app-shell">
    <aside className="main-sidebar">
      <div className="brand"><span className="brand-mark"><Boxes size={20}/></span><div><b>Agent-Team-OS</b><small>交付控制平面 · V0.3.1</small></div></div>
      <nav>
        <NavLink to="/projects"><FolderGit2 size={17}/><span>项目</span></NavLink>
        <div className="project-switcher"><label>当前项目<select aria-label="当前项目" value={projectId} onChange={(event) => navigate(projectPath(event.target.value, scopedSection?.section ?? "overview"))}>{projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label></div>
        {scopedPaths.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}
        <span className="nav-separator">全局目录</span>
        {globalSections.filter((section) => section.path !== "/projects").map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}
      </nav>
      <div className="system-state"><span className="pulse"/>{user.display_name}<small>{roleLabel(user.role)} · {user.username}</small><button className="text-button" onClick={logout} disabled={loggingOut}>{loggingOut ? "正在退出…" : "退出登录"}</button></div>
    </aside>
    <main>
      <header><div><p className="kicker">团队协作控制层</p><h1>{current.label}</h1><p className="page-description">{current.description}</p></div><div className="identity-chip"><Activity size={16}/><span>规划身份<br/><b>Codex 模拟 Hermes</b></span><span>执行身份<br/><b>Codex 命令行</b></span></div></header>
      <Outlet/>
    </main>
  </div>;
}

function roleLabel(role: string) {
  if (role === "administrator") return "管理员";
  if (role === "editor") return "编辑者";
  return "只读者";
}
