import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCommitHorizontal, LockKeyhole, RefreshCw, Save, ShieldCheck } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request, type AppSettings, type AppSettingsPatch } from "../../shared/api/client";
import { ConflictState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";

type LatestGateReports = components["schemas"]["LatestGateReports"];
type GateReport = components["schemas"]["GateReport"];

export function SettingsPage() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => request<AppSettings>("/v1/settings") });
  const releaseGates = useQuery({ queryKey: ["release-gates", "latest"], queryFn: () => request<LatestGateReports>("/v1/release-gates/latest") });
  const [draft, setDraft] = useState<AppSettings>();
  useEffect(() => { if (settings.data) setDraft(settings.data); }, [settings.data]);
  const save = useMutation({
    mutationFn: (patch: AppSettingsPatch) => request<AppSettings>("/v1/settings", { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: async (value) => { setDraft(value); await client.invalidateQueries({ queryKey: ["settings"] }); },
  });

  if (settings.isLoading || !draft) return <LoadingState label="正在读取运行策略…"/>;
  if (settings.error) return <ErrorState error={settings.error} retry={() => settings.refetch()}/>;
  const update = (field: keyof AppSettings, value: number) => setDraft((current) => current ? { ...current, [field]: value } : current);

  return <div className="settings-layout">
    <ReleaseGatePanel reports={releaseGates.data} loading={releaseGates.isLoading} error={releaseGates.error} onRefresh={() => releaseGates.refetch()}/>
    <section className="panel settings-form">
      <div className="panel-head"><span>安全运营参数</span><small>CAS 版本 {draft.version}</small></div>
      <ConflictState error={save.error}/>
      <div className="field-grid">
        <NumberField label="规划超时（秒）" value={draft.planning_timeout_seconds} min={30} max={300} onChange={(value) => update("planning_timeout_seconds", value)}/>
        <NumberField label="执行超时（秒）" value={draft.execution_timeout_seconds} min={60} max={600} onChange={(value) => update("execution_timeout_seconds", value)}/>
        <NumberField label="验证超时（秒）" value={draft.verification_timeout_seconds} min={10} max={300} onChange={(value) => update("verification_timeout_seconds", value)}/>
        <NumberField label="证据保留（天）" value={draft.evidence_retention_days} min={1} max={30} onChange={(value) => update("evidence_retention_days", value)}/>
      </div>
      <button className="primary button-icon" disabled={save.isPending} onClick={() => save.mutate({ expected_version: draft.version, planning_timeout_seconds: draft.planning_timeout_seconds, execution_timeout_seconds: draft.execution_timeout_seconds, verification_timeout_seconds: draft.verification_timeout_seconds, evidence_retention_days: draft.evidence_retention_days })}><Save size={16}/>保存参数</button>
      {save.error && !("status" in save.error && save.error.status === 409) && <ErrorState error={save.error}/>} 
    </section>
    <section className="panel locked-policy">
      <div className="panel-head"><span>系统硬限制</span><small><LockKeyhole size={13}/>界面不可修改</small></div>
      <Policy label="界面语言" values={[draft.language]}/><Policy label="允许修改路径" values={draft.allowed_paths}/><Policy label="固定机器验证" values={draft.verification_commands}/>
      <div className="policy-note">这些字段属于产品安全边界。Agent、用户需求和批量开发任务均不能覆盖。</div>
    </section>
  </div>;
}

function ReleaseGatePanel({ reports, loading, error, onRefresh }: { reports?: LatestGateReports; loading: boolean; error: Error | null; onRefresh: () => void }) {
  return <section className="panel release-gates-panel">
    <div className="panel-head"><span>发布双门禁</span><button className="text-button button-icon" onClick={onRefresh}><RefreshCw size={13}/>刷新报告</button></div>
    {loading && <LoadingState label="正在核验最新确定性与真实 Codex 报告…"/>}
    {error && <ErrorState error={error} retry={onRefresh}/>}
    {reports && <>
      <div className={`release-verdict verdict-${reports.combined.status}`}>
        <ShieldCheck size={24}/><div><span>当前代码发布结论</span><h2>{releaseVerdictLabel(reports.combined.status)}</h2><p>{reports.combined.reason}</p><code>{reports.combined.code}</code></div>
      </div>
      <div className="release-lock" aria-label="双门禁 Revision 锁">
        <GateReportCard title="确定性门禁" report={reports.deterministic}/>
        <div className="revision-coupler"><GitCommitHorizontal size={20}/><span>同 Revision</span></div>
        <GateReportCard title="真实 Codex 门禁" report={reports.live}/>
      </div>
      <p className="release-command">发布必须由本机命令执行：<code>uv run --extra live agent-team-os release</code>。界面只读取不可变报告，不会伪造或代替门禁。</p>
    </>}
  </section>;
}

function GateReportCard({ title, report }: { title: string; report: GateReport | null }) {
  if (!report) return <article className="gate-report missing"><div><span className="eyebrow">{title}</span><StatusBadge value="unknown"/></div><h3>缺少可解析报告</h3><p>运行发布命令生成新的 JSON 与 Markdown 证据。</p></article>;
  return <article className="gate-report"><div><span className="eyebrow">{title}</span><StatusBadge value={report.status}/></div><h3>{report.kind === "live" ? "真实 Codex 规划与执行" : "确定性模型边界与浏览器闭环"}</h3><dl><dt>生成时间</dt><dd>{formatReportTime(report.created_at)}</dd><dt>DEV Revision</dt><dd><code>{report.dev_revision}</code></dd><dt>ACWM Revision</dt><dd><code>{report.acwm_revision}</code></dd><dt>规划身份</dt><dd>{report.planning_identity}</dd><dt>执行身份</dt><dd>{report.execution_identity}</dd><dt>失败 / 警告 / 跳过</dt><dd>{report.fail} / {report.warn} / {report.skipped}</dd><dt>证据哈希</dt><dd><code>{report.evidence_sha256}</code></dd></dl>{report.kind === "deterministic" && <p className={report.browser_e2e && report.browser_restart_recovery ? "gate-proof verified" : "gate-proof invalid"}>浏览器闭环 {report.browser_e2e ? "已执行" : "缺失"} · 进程重启恢复 {report.browser_restart_recovery ? "已验证" : "缺失"}</p>}</article>;
}

function releaseVerdictLabel(status: LatestGateReports["combined"]["status"]) {
  if (status === "passed") return "可以发布";
  if (status === "failed") return "禁止发布";
  return "发布状态未知";
}

function formatReportTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium", hour12: false }).format(new Date(value));
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))}/><small>允许范围 {min}–{max}</small></label>;
}

function Policy({ label, values }: { label: string; values: readonly string[] }) {
  return <div className="policy-row"><b>{label}</b>{values.map((value) => <code key={value}>{value}</code>)}</div>;
}
