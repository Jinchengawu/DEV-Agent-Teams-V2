import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, Search } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { artifactTypeLabel, documentTitle } from "../../i18n";

type Document = components["schemas"]["KnowledgeDocument"];

export function KnowledgePage() {
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Document>();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const documents = useQuery({ queryKey: ["knowledge", query], queryFn: () => request<Document[]>(query ? `/v1/knowledge/search?q=${encodeURIComponent(query)}` : "/v1/knowledge/documents") });
  const create = useMutation({ mutationFn: () => request<Document>("/v1/knowledge/documents", { method: "POST", body: JSON.stringify({ title, media_type: "text/markdown", content }) }), onSuccess: async (document) => { setSelected(document); setTitle(""); setContent(""); await client.invalidateQueries({ queryKey: ["knowledge"] }); } });
  if (documents.isLoading) return <LoadingState label="正在读取可追溯知识…"/>;
  if (documents.error) return <ErrorState error={documents.error} retry={() => documents.refetch()}/>;
  return <div className="knowledge-layout">
    <section className="panel knowledge-index"><div className="panel-head"><span>知识目录</span><small>SQLite 全文检索</small></div><label className="search-field"><Search size={15}/><input aria-label="搜索知识" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索交付、验收编号或产物"/></label><div className="document-list">{documents.data?.map((document) => <button key={document.id} className={selected?.id === document.id ? "selected" : ""} onClick={() => setSelected(document)}><b>{documentTitle(document.title, document.artifact_type)}</b><small>{artifactTypeLabel(document.artifact_type)} · 修订 {document.revision}</small><code>{document.sha256.slice(0, 12)}</code></button>)}</div></section>
    <section className="panel document-reader"><div className="panel-head"><span>来源与正文</span><small>{selected ? `修订 ${selected.revision}` : "未选择"}</small></div>{selected ? <><h2>{documentTitle(selected.title, selected.artifact_type)}</h2><div className="source-chain">{selected.sources.length ? selected.sources.map((source) => <span key={`${source.source_kind}-${source.source_id}`}>{artifactTypeLabel(source.source_kind)} / {source.source_id}</span>) : <span>手工内容 / 用户提交</span>}</div><code className="document-hash">SHA-256 {selected.sha256}</code><pre>{selected.content}</pre></> : <EmptyState title="选择一份知识文档" detail="每份内容都显示来源、修订和内容哈希。系统不会以模型摘要替代原始产物。"/>}</section>
    <section className="panel knowledge-create"><div className="panel-head"><span>新增手工文档</span><small>Markdown</small></div><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="文档标题"/></label><label>正文<textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="输入 UTF-8 Markdown 内容"/></label><button className="primary button-icon" disabled={!title.trim() || !content.trim() || create.isPending} onClick={() => create.mutate()}><FilePlus2 size={16}/>保存内容版本</button>{create.error && <ErrorState error={create.error}/>}</section>
  </div>;
}

