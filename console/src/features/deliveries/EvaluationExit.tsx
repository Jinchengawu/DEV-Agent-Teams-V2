import { useState } from "react";
import { Button } from "antd";
import { Link } from "react-router-dom";
import { projectPath } from "../../entities/project/api";
import { ErrorState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import type { Delivery } from "../../entities/delivery/model";

export function EvaluationExit({ projectId, purpose, status, ready, boundEvaluation, canCancel, pending = false, error, onCancel }: {
  projectId: string;
  purpose: Delivery["purpose"];
  status: Delivery["status"];
  ready: boolean;
  boundEvaluation: boolean;
  canCancel: boolean;
  pending?: boolean;
  error?: Error | null;
  onCancel: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  if (purpose !== "onboarding_evaluation" || !ready || !boundEvaluation || !canCancel) return null;
  if (status === "cancelled") return <section className="surface-card inline-guidance">
    <b>评测已结束，候选与证据已保留</b><Link to={projectPath(projectId, "deliveries")}>发起正式交付</Link>
  </section>;
  if (status === "cancelling") return <section className="surface-card inline-guidance" role="status">正在结束评测，待清理完成后释放项目…</section>;
  if (status !== "awaiting_candidate_decision") return null;
  return <section className="surface-card inline-guidance">
    <b>引导已完成，当前评测仍占用项目</b>
    <p>结束评测后即可发起正式交付；本次候选与证据保留，不会 Apply。</p>
    <Button disabled={pending} onClick={() => setConfirm(true)}>结束评测并释放项目</Button>
    {error && <ErrorState error={error}/>}
    <ConfirmDialog open={confirm} title="结束当前评测" detail="结束当前评测并等待清理完成后释放项目。候选与证据保留，不批准发布，也不执行 Apply。" confirmLabel="确认结束评测" pending={pending} onCancel={() => setConfirm(false)} onConfirm={() => { setConfirm(false); onCancel(); }}/>
  </section>;
}
