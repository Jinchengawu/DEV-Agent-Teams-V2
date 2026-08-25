import { createContext, type ReactNode, useContext, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input } from "antd";
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
  const submit = () => {
    setProblem(null);
    authenticate.mutate();
  };
  return <IdentityFrame>
    <Card className="identity-form evidence-rail">
      <span className="identity-form-icon">{mode === "bootstrap" ? <ShieldCheck /> : <LockKeyhole />}</span>
      <div><p className="kicker">本地身份与权限</p><h1>{mode === "bootstrap" ? "初始化管理员" : "登录 Agent-Team-OS"}</h1></div>
      <p className="identity-copy">{mode === "bootstrap"
        ? "首次使用需创建唯一的初始管理员。密码仅以 scrypt 哈希保存。"
        : "登录后才能查看交付、证据与知识内容。"}</p>
      <Form layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item label="用户名" htmlFor="identity-username" required><Input id="identity-username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} autoComplete="username" /></Form.Item>
        {mode === "bootstrap" && <Form.Item label="显示名称" htmlFor="identity-display-name" required><Input id="identity-display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></Form.Item>}
        <Form.Item label="密码" htmlFor="identity-password" required extra="密码至少 12 位，并同时包含字母和数字。"><Input.Password id="identity-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} autoComplete={mode === "bootstrap" ? "new-password" : "current-password"} /></Form.Item>
        {problem && <Alert type="error" showIcon title={problem} role="alert" />}
        <Button type="primary" htmlType="submit" loading={authenticate.isPending}>
          {mode === "bootstrap" ? "创建并登录" : "登录控制平面"}
        </Button>
      </Form>
    </Card>
  </IdentityFrame>;
}

function IdentityFrame({ children }: { children: ReactNode }) {
  return <div className="identity-screen"><div className="identity-blueprint" />{children}</div>;
}
