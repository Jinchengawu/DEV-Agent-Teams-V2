import { createContext, type FormEvent, type ReactNode, useContext, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockKeyhole, ShieldCheck } from "lucide-react";
import { ApiProblem, request, type CurrentUser } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";

type IdentityContextValue = {
  user: CurrentUser;
  logout: () => void;
  loggingOut: boolean;
};

const IdentityContext = createContext<IdentityContextValue | null>(null);

export function useIdentity(): IdentityContextValue {
  const value = useContext(IdentityContext);
  if (!value) throw new Error("身份上下文尚未就绪");
  return value;
}

export function IdentityProvider({ user, children, logout = () => undefined, loggingOut = false }: { user: CurrentUser; children: ReactNode; logout?: () => void; loggingOut?: boolean }) {
  return <IdentityContext.Provider value={{ user, logout, loggingOut }}>{children}</IdentityContext.Provider>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const bootstrap = useQuery({
    queryKey: ["identity", "bootstrap"],
    queryFn: () => request<{ bootstrap_required: boolean }>("/v1/auth/bootstrap-status"),
    retry: false,
  });
  const session = useQuery({
    queryKey: ["identity", "session"],
    queryFn: () => request<CurrentUser>("/v1/auth/session"),
    enabled: bootstrap.data?.bootstrap_required === false,
    retry: false,
  });
  const logout = useMutation({
    mutationFn: () => request<void>("/v1/auth/logout", { method: "POST" }),
    onSuccess: () => queryClient.setQueryData(["identity", "session"], null),
  });

  if (bootstrap.isPending) return <IdentityFrame><LoadingState label="正在检查本地账户…" /></IdentityFrame>;
  if (bootstrap.error) return <IdentityFrame><ErrorState error={bootstrap.error} /></IdentityFrame>;
  if (bootstrap.data?.bootstrap_required) {
    return <IdentityForm mode="bootstrap" onAuthenticated={(user) => {
      queryClient.setQueryData(["identity", "bootstrap"], { bootstrap_required: false });
      queryClient.setQueryData(["identity", "session"], user);
    }} />;
  }
  if (session.isPending) return <IdentityFrame><LoadingState label="正在恢复登录会话…" /></IdentityFrame>;
  if (!session.data) {
    return <IdentityForm mode="login" onAuthenticated={(user) => {
      queryClient.setQueryData(["identity", "session"], user);
    }} />;
  }
  return <IdentityProvider user={session.data} logout={() => logout.mutate()} loggingOut={logout.isPending}>{children}</IdentityProvider>;
}

function IdentityForm({
  mode,
  onAuthenticated,
}: {
  mode: "bootstrap" | "login";
  onAuthenticated: (user: CurrentUser) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [displayName, setDisplayName] = useState("系统管理员");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const authenticate = useMutation({
    mutationFn: async () => {
      if (mode === "bootstrap") {
        await request<CurrentUser>("/v1/auth/bootstrap", {
          method: "POST",
          body: JSON.stringify({ username, display_name: displayName, password }),
        });
      }
      return request<CurrentUser>("/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
    },
    onSuccess: onAuthenticated,
    onError: (error) => setProblem(
      error instanceof ApiProblem
        ? `${error.problem.title ?? "操作失败"}：${error.problem.detail ?? error.message}`
        : "身份服务暂时无法完成操作。",
    ),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    authenticate.mutate();
  };
  return <IdentityFrame>
    <form className="identity-form" onSubmit={submit}>
      <span className="identity-form-icon">{mode === "bootstrap" ? <ShieldCheck /> : <LockKeyhole />}</span>
      <p className="kicker">本地身份与权限</p>
      <h1>{mode === "bootstrap" ? "初始化管理员" : "登录 Agent-Team-OS"}</h1>
      <p>{mode === "bootstrap"
        ? "首次使用需创建唯一的初始管理员。密码仅以 scrypt 哈希保存。"
        : "登录后才能查看交付、证据与知识内容。"}</p>
      <label><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} required minLength={3} autoComplete="username" /></label>
      {mode === "bootstrap" && <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>}
      <label><span>密码</span><input value={password} onChange={(event) => setPassword(event.target.value)} required minLength={12} type="password" autoComplete={mode === "bootstrap" ? "new-password" : "current-password"} /></label>
      <small>密码至少 12 位，并同时包含字母和数字。</small>
      {problem && <p className="form-problem" role="alert">{problem}</p>}
      <button className="primary-button" disabled={authenticate.isPending}>
        {authenticate.isPending ? "正在验证…" : mode === "bootstrap" ? "创建并登录" : "登录控制平面"}
      </button>
    </form>
  </IdentityFrame>;
}

function IdentityFrame({ children }: { children: ReactNode }) {
  return <div className="identity-screen"><div className="identity-blueprint" />{children}</div>;
}
