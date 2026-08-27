import { Bot, Database, FileCheck2, GitBranch, LayoutDashboard, LogOut, Settings, Workflow } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import mark from "../../assets/agent-team-os-mark.svg";
import inverseMark from "../../assets/agent-team-os-mark-inverse.svg";
import { useIdentity } from "../../features/identity/AuthGate";
import { ThemeToggle } from "../../shared/ui/ThemeToggle";

const workspaceSections = [
  { path: "/deliveries", label: "交付工作台", icon: GitBranch, description: "从目标进入可审批的交付闭环" },
  { path: "/board", label: "交付看板", icon: LayoutDashboard, description: "事件投影与合法状态命令" },
  { path: "/evidence", label: "证据", icon: FileCheck2, description: "不可变完整性账本" },
] as const;

const systemSections = [
  { path: "/knowledge", label: "知识中心", icon: Database, description: "可追溯版本内容" },
  { path: "/agents", label: "智能体实例", icon: Bot, description: "角色、部署与运行实例" },
  { path: "/orchestration", label: "可视化编排", icon: Workflow, description: "Pipeline、DAG 与人工 Gate" },
  { path: "/settings", label: "设置", icon: Settings, description: "安全运营参数与发布门禁" },
] as const;

const sections = [...workspaceSections, ...systemSections] as const;

export function AppShell() {
  const location = useLocation();
  const { user, logout, loggingOut } = useIdentity();
  const current = sections.find((section) => location.pathname.startsWith(section.path)) ?? sections[0];

  return <div className="app-shell">
    <aside className="main-sidebar">
      <NavLink className="brand" to="/deliveries" aria-label="Agent-Team-OS 交付工作台">
        <span className="brand-mark"><img className="brand-mark-light" src={mark} alt=""/><img className="brand-mark-dark" src={inverseMark} alt=""/></span>
        <span><b>Agent-Team-OS</b><small>交付控制平面 · V0.3.1</small></span>
      </NavLink>
      <nav aria-label="工作区导航"><span className="nav-label">工作区</span>{workspaceSections.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
      <div className="workspace-card"><span>当前工作区</span><b>内置后端沙箱</b><small>真实 API · 本地隔离执行</small></div>
      <nav aria-label="系统目录"><span className="nav-label">系统目录</span>{systemSections.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
      <div className="system-state"><span className="identity-state"><i/>{user.display_name}</span><small>{roleLabel(user.role)} · {user.username}</small><button className="text-button" onClick={logout} disabled={loggingOut}><LogOut size={14}/>{loggingOut ? "正在退出…" : "退出登录"}</button></div>
    </aside>
    <main className="app-main">
      <header className="app-context-bar"><div className="route-context"><span>控制平面　／</span><h1>{current.label}</h1><small>{current.description}</small></div><div className="context-actions"><ThemeToggle/><div className="identity-strip"><span>规划身份<b>Codex 模拟 Hermes</b></span><span>执行身份<b>Codex CLI</b></span></div></div></header>
      <Outlet/>
    </main>
  </div>;
}

function roleLabel(role: string) {
  if (role === "administrator") return "管理员";
  if (role === "editor") return "编辑者";
  return "只读者";
}
