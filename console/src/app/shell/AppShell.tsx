import { Activity, Bot, Boxes, Database, FileCheck2, GitBranch, LayoutDashboard, Settings, Workflow } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useIdentity } from "../../features/identity/AuthGate";

const sections = [
  { path: "/deliveries", label: "交付", icon: GitBranch, description: "真实 Git 候选与双审批" },
  { path: "/board", label: "看板", icon: LayoutDashboard, description: "事件投影与合法命令" },
  { path: "/orchestration", label: "可视化编排", icon: Workflow, description: "ACWM 线性旅程" },
  { path: "/agents", label: "智能体实例", icon: Bot, description: "实例与能力绑定" },
  { path: "/knowledge", label: "知识中心", icon: Database, description: "可追溯版本内容" },
  { path: "/evidence", label: "证据", icon: FileCheck2, description: "不可变完整性账本" },
  { path: "/settings", label: "设置", icon: Settings, description: "安全运营参数" },
] as const;

export function AppShell() {
  const location = useLocation();
  const { user, logout, loggingOut } = useIdentity();
  const current = sections.find((section) => location.pathname.startsWith(section.path)) ?? sections[0];
  return <div className="app-shell">
    <aside className="main-sidebar">
      <div className="brand"><span className="brand-mark"><Boxes size={20}/></span><div><b>Agent-Team-OS</b><small>交付控制平面 · V0.3.0</small></div></div>
      <nav>{sections.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path}><Icon size={17}/><span>{label}</span></NavLink>)}</nav>
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
