import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Filter, ShieldCheck } from "lucide-react";
import { artifactTypeLabel } from "../../i18n";
import { request, type EvidenceRecord } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { EvidenceSummary } from "./EvidenceSummary";

const kinds = ["", "journey", "plan-gate", "candidate", "diff", "verification", "candidate-gate", "apply-receipt"];

export function EvidencePage() {
  const client = useQueryClient();
  const [deliveryFilter, setDeliveryFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<EvidenceRecord>();
  const evidence = useQuery({ queryKey: ["evidence"], queryFn: () => request<EvidenceRecord[]>("/v1/evidence"), refetchInterval: 2500 });
  const verify = useMutation({ mutationFn: (id: string) => request<EvidenceRecord>(`/v1/evidence/${id}/verify`, { method: "POST" }), onSuccess: async (record) => { setSelected(record); await client.invalidateQueries({ queryKey: ["evidence"] }); } });
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
      <div className="panel-head"><span>全局证据账本</span><small>只追加 · 内容寻址</small></div>
      <EvidenceSummary records={records} />
      <div className="filter-bar"><Filter size={16}/><input aria-label="按交付筛选" placeholder="交付 ID" value={deliveryFilter} onChange={(event) => setDeliveryFilter(event.target.value)}/><select aria-label="按证据类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>{kinds.map((kind) => <option key={kind} value={kind}>{kind ? artifactTypeLabel(kind) : "全部类型"}</option>)}</select><select aria-label="按完整性筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部完整性</option><option value="verified">已验证</option><option value="invalid">无效</option><option value="unavailable">不可用</option></select></div>
      {filtered.length === 0 ? <EmptyState title="没有符合条件的真实证据" detail="清除筛选，或先完成交付阶段。系统不会补造缺失证据。"/> : <div className="ledger-table" role="table">{filtered.map((item) => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelected(item)}><StatusBadge value={item.status}/><span><b>{artifactTypeLabel(item.kind)}</b><small>{item.delivery_id} · {item.producer_identity}</small></span><code>{item.content_sha256?.slice(0, 16) ?? "无哈希"}</code></button>)}</div>}
    </section>
    <section className="panel evidence-inspector">
      <div className="panel-head"><span>完整性检查器</span><small>{selected?.id ?? "请选择证据"}</small></div>
      {!selected ? <EmptyState title="选择一条证据" detail="这里会显示来源、内容哈希、验证结果和原始结构化载荷。"/> : <>
        <div className="inspector-status"><StatusBadge value={selected.status}/><b>{artifactTypeLabel(selected.kind)}</b></div>
        <dl><dt>来源</dt><dd>{selected.source_kind} / {selected.source_id}</dd><dt>生产身份</dt><dd>{selected.producer_identity}</dd><dt>内容 SHA-256</dt><dd><code>{selected.content_sha256 ?? "未生成"}</code></dd><dt>验证时间</dt><dd>{selected.verified_at ?? "尚未验证"}</dd></dl>
        {selected.verification_error && <div className="repair-callout"><b>验证失败</b><span>{selected.verification_error}</span></div>}
        <pre className="payload-view">{JSON.stringify(selected.payload, null, 2)}</pre>
        <button className="secondary" disabled={verify.isPending} onClick={() => verify.mutate(selected.id)}><ShieldCheck size={16}/>重新验证当前证据</button>
        {verify.error && <ErrorState error={verify.error}/>} 
      </>}
    </section>
  </div>;
}
