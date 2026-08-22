import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockKeyhole, Save } from "lucide-react";
import { request, type AppSettings, type AppSettingsPatch } from "../../shared/api/client";
import { ConflictState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";

export function SettingsPage() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => request<AppSettings>("/v1/settings") });
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

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))}/><small>允许范围 {min}–{max}</small></label>;
}

function Policy({ label, values }: { label: string; values: readonly string[] }) {
  return <div className="policy-row"><b>{label}</b>{values.map((value) => <code key={value}>{value}</code>)}</div>;
}

