import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Filter, History, ShieldCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { artifactTypeLabel } from "../../i18n";
import { request, type EvidenceRecord } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { EvidenceSummary } from "./EvidenceSummary";
import { assertProjectScope, useProjectId } from "../../entities/project/api";

const kinds = ["", "journey", "plan-gate", "candidate", "diff", "verification", "candidate-gate", "apply-receipt"];
type EvidenceVerificationRecord = components["schemas"]["EvidenceVerificationRecord"];

export function EvidencePage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  const client = useQueryClient();
  const [deliveryFilter, setDeliveryFilter] = useState(() => searchParams.get("delivery_id") ?? "");
  const [kindFilter, setKindFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<EvidenceRecord>();
  const [copyNotice, setCopyNotice] = useState("");
  const evidence = useQuery({ queryKey: ["evidence", projectId], queryFn: async ({ signal }) => assertProjectScope(projectId, await request<EvidenceRecord[]>(`/v1/evidence?project_id=${encodeURIComponent(projectId)}`, { signal }), "证据账本"), refetchInterval: 2500 });
  const verificationHistory = useQuery({ queryKey: ["evidence-verifications", selected?.id], enabled: Boolean(selected), queryFn: ({ signal }) => request<EvidenceVerificationRecord[]>(`/v1/evidence/${selected?.id}/verifications`, { signal }) });
  const verify = useMutation({ mutationFn: (id: string) => request<EvidenceRecord>(`/v1/evidence/${id}/verify`, { method: "POST" }), onSuccess: async (record) => { setSelected(record); await Promise.all([client.invalidateQueries({ queryKey: ["evidence", projectId] }), client.invalidateQueries({ queryKey: ["evidence-verifications", record.id] })]); } });
  const filtered = useMemo(() => (evidence.data ?? []).filter((item) =>
    (!deliveryFilter || item.delivery_id.includes(deliveryFilter.trim())) &&
    (!kindFilter || item.kind === kindFilter) &&
    (!statusFilter || item.status === statusFilter),
  ), [deliveryFilter, evidence.data, kindFilter, statusFilter]);

  if (evidence.isLoading) return <LoadingState label="正在读取不可变证据账本…"/>;
  if (evidence.error) return <ErrorState error={evidence.error} retry={() => evidence.refetch()}/>;
  const records = evidence.data ?? [];

  return <div className="evidence-workbench">
    <section className="panel evidence-ledger">
      <div className="panel-head"><span>项目证据账本</span><small>{projectId} · 只追加 · 内容寻址</small></div>
      <EvidenceSummary records={records} />
      <div className="filter-bar"><Filter size={16}/><input aria-label="按交付筛选" placeholder="交付 ID" value={deliveryFilter} onChange={(event) => setDeliveryFilter(event.target.value)}/><select aria-label="按证据类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>{kinds.map((kind) => <option key={kind} value={kind}>{kind ? artifactTypeLabel(kind) : "全部类型"}</option>)}</select><select aria-label="按完整性筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部完整性</option><option value="verified">已验证</option><option value="invalid">无效</option><option value="unavailable">不可用</option></select></div>
      {filtered.length === 0 ? <EmptyState title="没有符合条件的真实证据" detail="清除筛选，或先完成交付阶段。系统不会补造缺失证据。"/> : <div className="ledger-table" role="table">{filtered.map((item) => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelected(item)}><StatusBadge value={item.status}/><span><b>{artifactTypeLabel(item.kind)}</b><small>{item.delivery_id} · {item.producer_identity}</small></span><code>{item.content_sha256?.slice(0, 16) ?? "无哈希"}</code></button>)}</div>}
    </section>
    <section className="panel evidence-inspector">
      <div className="panel-head"><span>完整性检查器</span><small>{selected?.id ?? "请选择证据"}</small></div>
      {!selected ? <EmptyState title="选择一条证据" detail="这里会显示来源、内容哈希、验证结果和原始结构化载荷。"/> : <>
        <div className="inspector-status"><StatusBadge value={selected.status}/><b>{artifactTypeLabel(selected.kind)}</b></div>
        <dl><dt>项目</dt><dd>{selected.project_id}</dd><dt>来源</dt><dd>{selected.source_kind} / {selected.source_id}</dd><dt>生产身份</dt><dd>{selected.producer_identity}</dd><dt>内容 SHA-256</dt><dd><code>{selected.content_sha256 ?? "未生成"}</code></dd><dt>验证时间</dt><dd>{selected.verified_at ?? "尚未验证"}</dd></dl>
        {selected.verification_error && <div className="repair-callout"><b>验证失败</b><span>{selected.verification_error}</span></div>}
        <details className="evidence-payload" open><summary>查看结构化载荷</summary><pre className="payload-view">{JSON.stringify(selected.payload, null, 2)}</pre></details>
        <div className="row-actions"><button className="secondary" disabled={verify.isPending} onClick={() => verify.mutate(selected.id)}><ShieldCheck size={16}/>重新验证当前证据</button><button className="secondary" disabled={!selected.content_sha256} onClick={async () => {
          if (!selected.content_sha256) return;
          try {
            await navigator.clipboard.writeText(selected.content_sha256);
            setCopyNotice("内容哈希已复制");
          } catch {
            setCopyNotice("浏览器未授权读取剪贴板，请从上方字段手动复制内容哈希。");
          }
        }}><Copy size={16}/>复制内容哈希</button><button className="secondary" onClick={() => downloadEvidence(selected)}><Download size={16}/>导出证据 JSON</button></div>
        {copyNotice && <p className="field-help" role="status">{copyNotice}</p>}
        <section className="verification-history"><div className="panel-subtitle"><History size={15}/>重新验证历史</div>{verificationHistory.isLoading ? <LoadingState label="正在读取验证历史…"/> : verificationHistory.error ? <ErrorState error={verificationHistory.error} retry={() => verificationHistory.refetch()}/> : verificationHistory.data?.length ? verificationHistory.data.map((item) => <article key={item.id}><StatusBadge value={item.status}/><div><b>{new Date(item.verified_at).toLocaleString("zh-CN")}</b><small>{item.error ?? "哈希与当前不可变内容一致"}</small></div><code>{item.id.slice(0, 12)}</code></article>) : <EmptyState title="尚无重新验证记录" detail="初始证据状态来自入账校验；点击“重新验证当前证据”后会在此追加验证记录。"/>}</section>
        {verify.error && <ErrorState error={verify.error}/>} 
      </>}
    </section>
  </div>;
}

function downloadEvidence(record: EvidenceRecord) {
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `evidence-${record.id}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
