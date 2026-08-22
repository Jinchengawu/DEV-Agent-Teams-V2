import type { ReactNode } from "react";
import { AlertTriangle, LoaderCircle } from "lucide-react";
import { ApiProblem } from "../api/client";

export function LoadingState({ label = "正在读取真实数据…" }: { label?: string }) {
  return <div className="state-box"><LoaderCircle className="spin" size={18}/><span>{label}</span></div>;
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="state-box state-empty"><b>{title}</b><span>{detail}</span></div>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  const problem = error instanceof ApiProblem ? error.problem : { detail: error.message };
  return <div className="state-box state-error"><AlertTriangle size={18}/><div><b>{problem.title ?? "数据读取失败"}</b><span>{problem.detail}</span>{problem.repair && <small>修复建议：{problem.repair}</small>}</div>{retry && <button onClick={retry}>重新加载</button>}</div>;
}

export function ConflictState({ error, children }: { error?: Error | null; children?: ReactNode }) {
  if (!(error instanceof ApiProblem) || error.status !== 409) return <>{children}</>;
  return <div className="conflict-banner"><b>数据版本已发生变化</b><span>{error.problem.repair ?? "刷新数据后重新提交。"}</span></div>;
}

