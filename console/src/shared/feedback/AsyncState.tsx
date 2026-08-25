import type { ReactNode } from "react";
import { Alert, Button, Empty, Result, Skeleton } from "antd";
import { ApiProblem } from "../api/client";

export function LoadingState({ label = "正在读取真实数据…" }: { label?: string }) {
  return <div className="atos-loading" role="status" aria-live="polite"><span className="atos-loading-label">{label}</span><Skeleton active paragraph={{ rows: 3 }}/></div>;
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span><b>{title}</b><br/>{detail}</span>}/>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  const problem = error instanceof ApiProblem ? error.problem : { detail: error.message };
  const detail = [problem.detail, problem.repair ? `修复建议：${problem.repair}` : null].filter(Boolean).join(" ");
  return <Result className="atos-state" status="error" title={problem.title ?? "数据读取失败"} subTitle={detail} extra={retry ? <Button type="primary" onClick={retry}>重新加载</Button> : undefined}/>;
}

export function ConflictState({ error, children }: { error?: Error | null; children?: ReactNode }) {
  if (!(error instanceof ApiProblem) || error.status !== 409) return <>{children}</>;
  return <Alert type="warning" showIcon title="数据版本已发生变化" description={error.problem.repair ?? "刷新数据后重新提交。"}/>;
}
