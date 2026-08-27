import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Checkbox, Input, Select } from "antd";
import { Archive, BookMarked, BookPlus, FilePlus2, FileText, FolderPlus, MessageSquare, RefreshCw, Search } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { ApiProblem, request } from "../../shared/api/client";
import { ConflictState, EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { useProjectId } from "../../entities/project/api";

type Space = components["schemas"]["Space"];
type Document = components["schemas"]["Document"];
type Revision = components["schemas"]["Revision"];
type Comment = components["schemas"]["Comment"];
type SpaceCreate = components["schemas"]["SpaceCreate"];
type DocumentCreate = components["schemas"]["DocumentCreate"];
type DocumentPatch = components["schemas"]["DocumentPatch"];
type CommentCreate = components["schemas"]["CommentCreate"];
type RevisionRestoreRequest = components["schemas"]["RevisionRestoreRequest"];
type KnowledgeSearchHit = components["schemas"]["KnowledgeSearchHit"];
type KnowledgeActivityItem = components["schemas"]["KnowledgeActivityItem"];
type KnowledgeDerivationCreate = components["schemas"]["KnowledgeDerivationCreate"];
type KnowledgeDerivationResult = components["schemas"]["KnowledgeDerivationResult"];

type MarkdownPayload = {
  format: "markdown";
  text: string;
};

type ProjectDocumentPayload = {
  schema: "project-document-v1";
  artifact_key: string;
  markdown: string;
};

const markdownPayload = (text: string): MarkdownPayload => ({
  format: "markdown",
  text,
});

function isMarkdownPayload(value: unknown): value is MarkdownPayload {
  return Boolean(
    value &&
      typeof value === "object" &&
      "format" in value &&
      (value as Record<string, unknown>).format === "markdown" &&
      "text" in value &&
      typeof (value as Record<string, unknown>).text === "string",
  );
}

function isProjectDocumentPayload(value: unknown): value is ProjectDocumentPayload {
  return Boolean(
    value
      && typeof value === "object"
      && "schema" in value
      && (value as Record<string, unknown>).schema === "project-document-v1"
      && "artifact_key" in value
      && typeof (value as Record<string, unknown>).artifact_key === "string"
      && "markdown" in value
      && typeof (value as Record<string, unknown>).markdown === "string",
  );
}

function markdownFromContent(value: unknown): string {
  if (isMarkdownPayload(value)) {
    return value.text;
  }
  if (isProjectDocumentPayload(value)) {
    return value.markdown;
  }
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function isConflictError(error: unknown): error is ApiProblem {
  return error instanceof ApiProblem && error.status === 409;
}

function sourceKindLabel(value: string) {
  if (value === "wiki") return "项目文档";
  if (value === "evidence") return "不可变证据";
  if (value === "provider-snapshot") return "外部来源快照";
  return `来源 ${value}`;
}

const documentKinds = [
  ["product-requirement", "产品需求"],
  ["delivery-plan", "交付计划"],
  ["design-spec", "设计说明"],
  ["frontend-technical", "前端技术"],
  ["backend-api", "后端 API"],
  ["test-plan", "测试计划"],
  ["test-report", "测试报告"],
  ["project-general", "项目通用"],
] as const;

const documentKindFilterOptions = [
  { value: "", label: "全部类型" },
  ...documentKinds.map(([value, label]) => ({ value, label })),
];

const sourceKindFilterOptions = [
  { value: "", label: "全部来源" },
  { value: "manual", label: "人工" },
  { value: "agent-publication", label: "Agent 发布" },
  { value: "legacy-migrated", label: "Legacy 迁移" },
];

const documentKindCreateOptions = documentKinds.map(([value, label]) => ({ value, label }));

const searchGroups = [
  ["project-document", "项目文档"],
  ["evidence", "Evidence"],
  ["external-source", "外部来源"],
] as const;

function documentKindLabel(value: string) {
  return documentKinds.find(([kind]) => kind === value)?.[1] ?? value;
}

export function KnowledgePage() {
  const projectId = useProjectId();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [selectedSpaceId, setSelectedSpaceId] = useState("");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string>();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [documentKind, setDocumentKind] = useState("");
  const [roleKey, setRoleKey] = useState("");
  const [deliveryFilter, setDeliveryFilter] = useState("");
  const [sourceKind, setSourceKind] = useState("");

  const [newSpaceName, setNewSpaceName] = useState("");
  const [newSpaceDescription, setNewSpaceDescription] = useState("");

  const [newDocumentTitle, setNewDocumentTitle] = useState("");
  const [newDocumentContent, setNewDocumentContent] = useState("");
  const [newDocumentKind, setNewDocumentKind] = useState("project-general");
  const [newDocumentRole, setNewDocumentRole] = useState("");
  const [newDocumentDelivery, setNewDocumentDelivery] = useState("");

  const [editorTitle, setEditorTitle] = useState("");
  const [editorContent, setEditorContent] = useState("");

  const [commentText, setCommentText] = useState("");
  const [pendingRestore, setPendingRestore] = useState<{ revision: number; contentSha256: string }>();

  const spaces = useQuery({
    queryKey: ["wiki-spaces", projectId, includeArchived],
    queryFn: ({ signal }) => request<Space[]>(`/v1/wiki/spaces?project_id=${encodeURIComponent(projectId)}&include_global=true${includeArchived ? "&include_archived=true" : ""}`, { signal }),
  });

  useEffect(() => {
    setSelectedSpaceId("");
    setSelectedDocumentId(undefined);
    setSearch("");
    setIncludeArchived(false);
    setDocumentKind("");
    setRoleKey("");
    setDeliveryFilter("");
    setSourceKind("");
  }, [projectId]);

  useEffect(() => {
    if (!spaces.data?.length) return;
    if (selectedSpaceId && spaces.data.some((space) => space.id === selectedSpaceId)) return;
    const preferred = spaces.data.find((space) =>
      space.space_kind === "project-documents" && space.lifecycle_status === "active"
    ) ?? spaces.data.find((space) => space.lifecycle_status === "active") ?? spaces.data[0];
    setSelectedSpaceId(preferred.id);
  }, [spaces.data, selectedSpaceId]);

  const orderedSpaces = useMemo(() => [...(spaces.data ?? [])].sort((left, right) => {
    const rank = (space: Space) => space.space_kind === "project-documents" ? 0 : space.space_kind === "custom" ? 1 : 2;
    return rank(left) - rank(right) || left.name.localeCompare(right.name, "zh-CN");
  }), [spaces.data]);

  const selectedSpace = useMemo(
    () => spaces.data?.find((space) => space.id === selectedSpaceId),
    [selectedSpaceId, spaces.data],
  );
  const selectedSpaceReadOnly = selectedSpace?.space_kind === "legacy-archive"
    || selectedSpace?.lifecycle_status === "archived";

  const listDocumentsPath = useMemo(() => {
    if (!selectedSpaceId) {
      return "";
    }
    const parameters = new URLSearchParams({ space_id: selectedSpaceId });
    if (documentKind) parameters.set("document_kind", documentKind);
    if (roleKey.trim()) parameters.set("role_key", roleKey.trim());
    if (deliveryFilter.trim()) parameters.set("delivery_id", deliveryFilter.trim());
    if (sourceKind) parameters.set("source_kind", sourceKind);
    if (includeArchived) parameters.set("include_archived", "true");
    return `/v1/wiki/documents?${parameters.toString()}`;
  }, [deliveryFilter, documentKind, includeArchived, roleKey, selectedSpaceId, sourceKind]);

  const documentQueryKey = ["wiki-documents", projectId, selectedSpaceId, documentKind, roleKey, deliveryFilter, sourceKind, includeArchived] as const;
  const documents = useQuery({
    queryKey: documentQueryKey,
    enabled: spaces.isSuccess && Boolean(listDocumentsPath),
    queryFn: ({ signal }) => request<Document[]>(listDocumentsPath, { signal }),
  });

  const unifiedSearch = useQuery({
    queryKey: ["knowledge-search", projectId, search],
    enabled: Boolean(search.trim()),
    queryFn: ({ signal }) => request<KnowledgeSearchHit[]>(`/v1/knowledge/search?project_id=${encodeURIComponent(projectId)}&include_global=true&q=${encodeURIComponent(search.trim())}`, { signal }),
  });

  const activity = useQuery({
    queryKey: ["knowledge-activity", projectId],
    queryFn: ({ signal }) =>
      request<KnowledgeActivityItem[]>(
        `/v1/knowledge/activity?project_id=${encodeURIComponent(projectId)}&include_global=true&limit=50`,
        { signal },
      ),
  });

  const deriveSource = useMutation({
    mutationFn: ({ item, targetSpaceId }: { item: KnowledgeActivityItem; targetSpaceId: string }) =>
      request<KnowledgeDerivationResult>("/v1/knowledge/derivations", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          source_kind: item.source_kind,
          source_id: item.source_id,
          expected_source_sha256: item.content_sha256,
          target_space_id: targetSpaceId,
          title: item.title,
        } as KnowledgeDerivationCreate),
      }),
    onSuccess: async (result) => {
      setSelectedSpaceId(result.document.space_id);
      setSelectedDocumentId(result.document.id);
      client.setQueryData<Document[]>(
        ["wiki-documents", projectId, result.document.space_id],
        (current) => [
          result.document,
          ...(current ?? []).filter((item) => item.id !== result.document.id),
        ],
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ["wiki-documents"] }),
        client.invalidateQueries({ queryKey: ["knowledge-activity", projectId] }),
      ]);
    },
  });

  const selectedDocument = useMemo(
    () => documents.data?.find((document) => document.id === selectedDocumentId),
    [documents.data, selectedDocumentId],
  );

  useEffect(() => {
    if (selectedDocumentId && documents.data && !documents.data.some((document) => document.id === selectedDocumentId)) {
      setSelectedDocumentId(undefined);
    }
  }, [documents.data, selectedDocumentId]);

  const currentRevision = useQuery({
    queryKey: ["wiki-document-revision", selectedDocument?.id, selectedDocument?.current_revision],
    enabled: Boolean(selectedDocument),
    queryFn: ({ signal }) => request<Revision>(`/v1/wiki/documents/${selectedDocument!.id}/revisions/${selectedDocument!.current_revision}`, { signal }),
  });

  const revisions = useQuery({
    queryKey: ["wiki-document-revisions", selectedDocument?.id],
    enabled: Boolean(selectedDocument),
    queryFn: ({ signal }) => request<Revision[]>(`/v1/wiki/documents/${selectedDocument!.id}/revisions`, { signal }),
  });

  const comments = useQuery({
    queryKey: ["wiki-document-comments", selectedDocument?.id],
    enabled: Boolean(selectedDocument),
    queryFn: ({ signal }) => request<Comment[]>(`/v1/wiki/documents/${selectedDocument!.id}/comments`, { signal }),
  });

  const createSpace = useMutation({
    mutationFn: (payload: SpaceCreate) =>
      request<Space>("/v1/wiki/spaces", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: async (space) => {
      setNewSpaceName("");
      setNewSpaceDescription("");
      setSelectedSpaceId(space.id);
      await client.invalidateQueries({ queryKey: ["wiki-spaces", projectId] });
    },
  });

  const createDocument = useMutation({
    mutationFn: (payload: Omit<DocumentCreate, "content"> & { content: MarkdownPayload }) =>
      request<Document>("/v1/wiki/documents", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: async (document) => {
      setNewDocumentTitle("");
      setNewDocumentContent("");
      setNewDocumentKind("project-general");
      setNewDocumentRole("");
      setNewDocumentDelivery("");
      client.setQueryData<Document[]>(documentQueryKey, (current) => [
        document,
        ...(current ?? []).filter((item) => item.id !== document.id),
      ]);
      setSelectedDocumentId(document.id);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["wiki-documents"] }),
        client.invalidateQueries({ queryKey: ["wiki-document-revisions", document.id] }),
        client.invalidateQueries({ queryKey: ["wiki-document-comments", document.id] }),
      ]);
    },
  });

  const updateDocument = useMutation({
    mutationFn: (payload: { id: string; expectedVersion: number; title: string; content: MarkdownPayload }) =>
      request<Document>(`/v1/wiki/documents/${payload.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: payload.expectedVersion,
          title: payload.title,
          content: payload.content,
        } as DocumentPatch),
      }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["wiki-documents"] }),
        client.invalidateQueries({ queryKey: ["wiki-document-revisions", selectedDocument?.id] }),
        client.invalidateQueries({ queryKey: ["wiki-document-comments", selectedDocument?.id] }),
      ]);
    },
  });

  const restoreRevision = useMutation({
    mutationFn: ({ documentId, revision, expectedVersion }: { documentId: string; revision: number; expectedVersion: number }) =>
      request<Document>(`/v1/wiki/documents/${documentId}/revisions/${revision}/restore`, {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion } as RevisionRestoreRequest),
      }),
    onSuccess: async (document) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["wiki-documents"] }),
        client.invalidateQueries({ queryKey: ["wiki-document-revisions", document.id] }),
        client.invalidateQueries({ queryKey: ["wiki-document-comments", document.id] }),
      ]);
      setSelectedDocumentId(document.id);
    },
  });

  const addComment = useMutation({
    mutationFn: ({ documentId, body }: { documentId: string; body: string }) =>
      request<Comment>(`/v1/wiki/documents/${documentId}/comments`, {
        method: "POST",
        body: JSON.stringify({ body } as CommentCreate),
      }),
    onSuccess: async () => {
      setCommentText("");
      await client.invalidateQueries({ queryKey: ["wiki-document-comments", selectedDocument?.id] });
    },
  });

  useEffect(() => {
    if (!selectedDocument) {
      setEditorTitle("");
      setEditorContent("");
      return;
    }
    setEditorTitle(selectedDocument.title);
    setEditorContent(currentRevision.data ? markdownFromContent(currentRevision.data.content) : "");
  }, [selectedDocument?.id, currentRevision.data]);

  const operationErrors = [
    createSpace.error,
    createDocument.error,
    updateDocument.error,
    restoreRevision.error,
    addComment.error,
    deriveSource.error,
  ];

  const conflictError = operationErrors.find(isConflictError);
  const operationError = operationErrors.find((error): error is Error => Boolean(error) && !isConflictError(error));

  if (spaces.isLoading) {
    return <LoadingState label="正在读取知识空间…" />;
  }

  if (spaces.error) {
    return <ErrorState error={spaces.error} retry={() => spaces.refetch()} />;
  }

  return (
    <div className="knowledge-layout">
      <section className="panel knowledge-activity" aria-label="项目知识动态">
        <div className="panel-head">
          <span>项目知识动态</span>
          <small>交付证据、Wiki 修订与外部来源按时间汇总，不改变各自权威数据</small>
        </div>
        {activity.isLoading ? (
          <LoadingState label="正在读取项目知识动态…" />
        ) : activity.error ? (
          <ErrorState error={activity.error} retry={() => activity.refetch()} />
        ) : activity.data?.length ? (
          <div className="knowledge-activity-list" role="list">
            {activity.data.map((item) => (
              <article
                key={`${item.source_kind}:${item.source_id}`}
                className="knowledge-activity-item"
                role="listitem"
              >
                <span className="knowledge-source-label">{sourceKindLabel(item.source_kind)}</span>
                <a className="knowledge-activity-link" href={item.source_link || undefined}>
                  <b>{item.title}</b>
                </a>
                <p>{item.summary || "该来源没有可展示的文本摘要。"}</p>
                <small>
                  {new Date(item.occurred_at).toLocaleString("zh-CN")} · 修订 {item.revision} · SHA-256 {item.content_sha256?.slice(0, 16) ?? "未提供"}
                </small>
                {item.source_kind !== "wiki" && (
                  <Button
                    className="button-icon knowledge-derive-button"
                    aria-label={`提炼“${item.title}”为 Wiki`}
                    disabled={
                      deriveSource.isPending ||
                      !selectedSpaceId ||
                      !item.content_sha256
                    }
                    title={selectedSpaceId ? "保留来源 Revision 与 SHA-256，创建可编辑 Wiki" : "请先创建或选择项目知识空间"}
                    onClick={() => {
                      if (selectedSpaceId) {
                        deriveSource.mutate({ item, targetSpaceId: selectedSpaceId });
                      }
                    }}
                  >
                    <BookPlus size={15} />提炼为 Wiki
                  </Button>
                )}
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            title="当前项目还没有知识来源"
            detail="完成交付、创建 Wiki 或同步外部来源后，真实记录会自动出现在这里。"
          />
        )}
      </section>
      <section className="panel knowledge-index">
        <div className="panel-head">
          <span>知识空间</span>
          <small>左栏展示空间、全文检索与创建空间命令</small>
        </div>

        <div className="knowledge-space-list" role="list" aria-label="知识空间列表">
          {orderedSpaces.length ? orderedSpaces.map((space) => (
            <Button
              key={space.id}
              className={`knowledge-space-item ${selectedSpaceId === space.id ? "selected" : ""}`}
              onClick={() => {
                setSelectedSpaceId(space.id);
                setSelectedDocumentId(undefined);
              }}
            >
              <b>{space.name}</b>
              <small>{space.space_kind === "project-documents" ? "标准项目文档" : space.space_kind === "legacy-archive" ? "Legacy Archive · 只读" : "自定义空间"}</small>
              <small>协议 ID: {space.id}</small>
              <small>版本: {space.version}</small>
            </Button>
          )) : <EmptyState title="当前无知识空间" detail="请先在下方创建知识空间后再写入文档。" />}
        </div>

        <div className="panel-head"><span>创建知识空间</span><small>协议 ID 不可手工伪造</small></div>
        <label>空间名称<Input value={newSpaceName} onChange={(event) => setNewSpaceName(event.target.value)} placeholder="例如：交付经验库" /></label>
        <label>说明<Input.TextArea value={newSpaceDescription} onChange={(event) => setNewSpaceDescription(event.target.value)} placeholder="例如：记录交付策略与知识版本" /></label>
        <Button
          type="primary"
          className="button-icon"
          disabled={createSpace.isPending || !newSpaceName.trim()}
          onClick={() =>
            createSpace.mutate({
              name: newSpaceName.trim(),
              description: newSpaceDescription.trim(),
              scope_kind: "project",
              project_id: projectId,
            } as SpaceCreate)
          }
        >
          <FolderPlus size={16} />创建空间
        </Button>
        {createSpace.error && <ErrorState error={createSpace.error} />}

        <Checkbox
          className="knowledge-archive-toggle"
          checked={includeArchived}
          onChange={(event) => setIncludeArchived(event.target.checked)}
        >
          <span><Archive size={14}/>管理员显式查看 legacy archive</span>
        </Checkbox>

        <label className="search-field">
          <Search size={15} />
          <Input
            aria-label="全文搜索"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索项目 Wiki、全局 Wiki、证据与外部快照"
          />
        </label>

        {search.trim() && <div className="knowledge-search-results">
          <div className="panel-head"><span>统一来源检索</span><small>保留各来源权威语义</small></div>
          {unifiedSearch.isLoading ? <LoadingState label="正在检索多来源知识…"/> : unifiedSearch.error ? <ErrorState error={unifiedSearch.error} retry={() => unifiedSearch.refetch()}/> : unifiedSearch.data?.length ? searchGroups.map(([group, label]) => {
            const hits = unifiedSearch.data.filter((hit) => hit.group === group);
            if (!hits.length) return null;
            return <section className="knowledge-search-group" key={group} aria-label={`${label}搜索结果`}><h3>{label}</h3>{hits.map((hit) => <a key={`${hit.source_kind}:${hit.source_id}`} href={hit.source_link || undefined} className="knowledge-search-hit"><b>{hit.title}</b><span>{sourceKindLabel(hit.source_kind)} · 修订 {hit.revision}</span><small>{hit.summary}</small><code>{hit.content_sha256?.slice(0, 16) ?? "无内容哈希"}</code></a>)}</section>;
          }) : <EmptyState title="没有匹配的来源" detail="系统不会为缺失知识或证据生成模拟结果。"/>}
        </div>}

        <div className="knowledge-filters" aria-label="项目文档筛选">
          <label>文档类型<Select aria-label="文档类型" value={documentKind} onChange={setDocumentKind} options={documentKindFilterOptions} /></label>
          <label>角色<Input aria-label="角色" value={roleKey} onChange={(event) => setRoleKey(event.target.value)} placeholder="例如 product-manager" /></label>
          <label>Delivery<Input aria-label="Delivery" value={deliveryFilter} onChange={(event) => setDeliveryFilter(event.target.value)} placeholder="Delivery ID" /></label>
          <label>来源<Select aria-label="来源" value={sourceKind} onChange={setSourceKind} options={sourceKindFilterOptions} /></label>
        </div>

        <div className="panel-head"><span>当前空间文档</span><small>{selectedSpaceId || "未选择空间"}</small></div>
        <div className="document-list" role="list" aria-label="当前空间文档列表">
          {documents.isLoading && selectedSpaceId ? <LoadingState label="正在读取当前空间文档…" /> : documents.error ? <ErrorState error={documents.error} retry={() => documents.refetch()} /> : documents.data?.length ? documents.data.map((document) => (
            <Button
              key={document.id}
              className={`knowledge-doc-item ${selectedDocumentId === document.id ? "selected" : ""}`}
              aria-label={`文档 ${document.title}`}
              onClick={() => setSelectedDocumentId(document.id)}
            >
              <b>{document.title}</b>
              <small>{documentKindLabel(document.document_kind ?? "project-general")} · {document.role_key ?? "未指定角色"}</small>
              <small>{document.delivery_id ? `Delivery ${document.delivery_id}` : "项目级文档"} · {document.source_kind}</small>
              <small>文档 ID: {document.id}</small>
              <small>修订: {document.current_revision} · 协议版本: {document.version}</small>
            </Button>
          )) : <EmptyState title="当前空间无文档" detail="选择一个空间后创建并编辑 Markdown 文档。" />}
        </div>
      </section>

      <section className="panel document-reader">
        <div className="panel-head"><span>正文与修订号</span><small>中栏按 current_revision 拉取真实版本</small></div>
        <ConflictState error={conflictError} />
        {operationError && <ErrorState error={operationError} />}

        {selectedDocument ? (
          currentRevision.isLoading ? <LoadingState label="正在读取修订正文…" /> :
          currentRevision.error ? <ErrorState error={currentRevision.error} retry={() => currentRevision.refetch()} /> :
          currentRevision.data ? (
            <>
              <h2>{selectedDocument.title}</h2>
              <p className="field-help">协议 ID: <code>{selectedDocument.id}</code></p>
              <p className="field-help">修订号: <code>{currentRevision.data.revision}</code></p>
              <p className="field-help">内容 SHA-256: <code>{currentRevision.data.content_sha256}</code></p>
              <div className="revision-provenance">
                <b>Revision Provenance</b>
                <p>生产者 {currentRevision.data.provenance?.producer_kind ?? "legacy"} / {currentRevision.data.provenance?.producer_id ?? "legacy-system"} · AgentRun {currentRevision.data.provenance?.agent_run_id ?? "无（人工修订）"}</p>
                <p>运行身份 {currentRevision.data.provenance?.runtime_identity ?? "human"} · Binding {currentRevision.data.provenance?.binding_site ?? "—"} · Contract {currentRevision.data.provenance?.contract_id ?? "—"}</p>
                <p>原始 Artifact SHA-256 <code>{currentRevision.data.provenance?.source_artifact_sha256 ?? "人工修订无原始 Artifact"}</code></p>
              </div>
              <pre className="document-content">{markdownFromContent(currentRevision.data.content)}</pre>
            </>
          ) : (
            <EmptyState title="无法读取正文" detail="已选择文档，但当前修订未返回正文。" />
          )
        ) : (
          <EmptyState title="未选择文档" detail="请在左栏点击文档后加载真实修订版本。" />
        )}
      </section>

      <section className="panel knowledge-create">
        <div className="panel-head"><span>文档操作</span><small>右栏执行真实命令</small></div>
        {conflictError && <ConflictState error={conflictError} />}
        {selectedSpaceReadOnly && <div className="knowledge-readonly"><Archive size={17}/><div><b>Legacy Archive 始终只读</b><span>这里只保留迁移映射；不能创建、编辑、恢复修订或发表评论。</span></div></div>}

        <div className="panel-subtitle">创建文档</div>
        <label>标题<Input value={newDocumentTitle} onChange={(event) => setNewDocumentTitle(event.target.value)} disabled={!selectedSpaceId || selectedSpaceReadOnly} placeholder="文档标题" /></label>
        <label>文档类型<Select aria-label="新建文档类型" value={newDocumentKind} onChange={setNewDocumentKind} disabled={!selectedSpaceId || selectedSpaceReadOnly} options={documentKindCreateOptions} /></label>
        <label>角色（可选）<Input value={newDocumentRole} onChange={(event) => setNewDocumentRole(event.target.value)} disabled={!selectedSpaceId || selectedSpaceReadOnly} placeholder="例如 frontend-engineer" /></label>
        <label>Delivery（可选）<Input value={newDocumentDelivery} onChange={(event) => setNewDocumentDelivery(event.target.value)} disabled={!selectedSpaceId || selectedSpaceReadOnly} placeholder="Delivery ID" /></label>
        <label><BookMarked size={14} />Markdown 正文<Input.TextArea value={newDocumentContent} onChange={(event) => setNewDocumentContent(event.target.value)} disabled={!selectedSpaceId || selectedSpaceReadOnly} placeholder="使用 Markdown 正文" /></label>
        <Button
          type="primary"
          className="button-icon"
          disabled={!selectedSpaceId || selectedSpaceReadOnly || createDocument.isPending || !newDocumentTitle.trim() || !newDocumentContent.trim()}
          onClick={() =>
            createDocument.mutate({
              space_id: selectedSpaceId,
              parent_id: null,
              title: newDocumentTitle.trim(),
              document_kind: newDocumentKind,
              role_key: newDocumentRole.trim() || null,
              delivery_id: newDocumentDelivery.trim() || null,
              content: markdownPayload(newDocumentContent),
              asset_references: [],
            } as Omit<DocumentCreate, "content"> & { content: MarkdownPayload })
          }
        >
          <FilePlus2 size={16} />创建文档
        </Button>
        {createDocument.error && !isConflictError(createDocument.error) && <ErrorState error={createDocument.error} />}

        <div className="panel-subtitle">编辑当前文档（expected_version）</div>
        {selectedDocument ? (
          <>
            <label>标题<Input value={editorTitle} onChange={(event) => setEditorTitle(event.target.value)} disabled={selectedSpaceReadOnly} placeholder="文档标题" /></label>
            <label><MessageSquare size={14} />Markdown 正文<Input.TextArea value={editorContent} onChange={(event) => setEditorContent(event.target.value)} disabled={selectedSpaceReadOnly} placeholder="Markdown 正文" /></label>
            <Button
              className="button-icon"
              disabled={selectedSpaceReadOnly || updateDocument.isPending || !editorTitle.trim() || !editorContent.trim()}
              onClick={() =>
                updateDocument.mutate({
                  id: selectedDocument.id,
                  expectedVersion: selectedDocument.version,
                  title: editorTitle.trim(),
                  content: markdownPayload(editorContent),
                })
              }
            >
              <FileText size={16} />保存标题与正文
            </Button>
            {updateDocument.error && !isConflictError(updateDocument.error) && <ErrorState error={updateDocument.error} />}
          </>
        ) : <EmptyState title="未选择可编辑文档" detail="先从左栏选择文档后才能编辑标题和 Markdown 正文。" />}

        <div className="panel-subtitle">版本列表与恢复</div>
        {selectedDocument ? (
          revisions.isLoading ? <LoadingState label="正在读取修订历史…" /> :
          revisions.error ? <ErrorState error={revisions.error} retry={() => revisions.refetch()} /> :
          revisions.data?.length ? (
            <div className="revision-list" role="list" aria-label="版本列表">
              {revisions.data.map((revision) => (
                <article key={revision.revision} className="revision-item" role="listitem">
                  <div><b>修订 {revision.revision}</b><small>{revision.provenance?.producer_kind ?? "legacy"} / {revision.provenance?.producer_id ?? "legacy-system"} · 创建于 {revision.created_at}</small></div>
                  <small>SHA-256 {revision.content_sha256.slice(0, 12)}</small>
                  <Button
                    disabled={selectedSpaceReadOnly || restoreRevision.isPending || revision.revision === selectedDocument.current_revision}
                    onClick={() => setPendingRestore({ revision: revision.revision, contentSha256: revision.content_sha256 })}
                  >
                    <RefreshCw size={14} />恢复该版本
                  </Button>
                </article>
              ))}
            </div>
          ) : <EmptyState title="未返回版本记录" detail="当前文档尚未形成可恢复历史。" />
        ) : <EmptyState title="未选择文档" detail="先选择文档查看版本列表后再恢复。" />}

        <div className="panel-subtitle">评论</div>
        {selectedDocument ? (
          <>
            {comments.isLoading ? <LoadingState label="正在读取评论…" /> :
            comments.error ? <ErrorState error={comments.error} retry={() => comments.refetch()} /> :
            comments.data?.length ? (
              <div className="comment-list" role="list" aria-label="评论列表">
                {comments.data.map((comment) => (
                  <article key={comment.id} className="comment-item" role="listitem">
                    <div className="comment-meta">{comment.id} · 作者 {comment.author_id} · 处理状态 {comment.resolved ? "已解析" : "未解析"}</div>
                    <p>{comment.body}</p>
                    <small>版本 {comment.version} · {comment.created_at}</small>
                  </article>
                ))}
              </div>
            ) : <EmptyState title="暂无评论" detail="当前文档还未形成评论流。" />}
            <label>添加评论<Input.TextArea value={commentText} onChange={(event) => setCommentText(event.target.value)} disabled={selectedSpaceReadOnly} placeholder="输入评论正文" /></label>
            <Button
              className="button-icon"
              disabled={selectedSpaceReadOnly || addComment.isPending || !commentText.trim()}
              onClick={() => selectedDocument && addComment.mutate({ documentId: selectedDocument.id, body: commentText })}
            >
              <MessageSquare size={16} />添加评论
            </Button>
            {addComment.error && !isConflictError(addComment.error) && <ErrorState error={addComment.error} />}
          </>
        ) : <EmptyState title="未选择文档" detail="先选择文档读取评论并提交。" />}
      </section>
      <ConfirmDialog open={Boolean(pendingRestore)} title={`恢复“${selectedDocument?.title ?? "当前文档"}”到修订 ${pendingRestore?.revision ?? ""}`} detail={`系统会以该历史内容创建一个新的当前修订，不会改写或删除原修订。目标内容 SHA-256：${pendingRestore?.contentSha256.slice(0, 16) ?? ""}。`} confirmLabel={`确认恢复到修订 ${pendingRestore?.revision ?? ""}`} tone="danger" pending={restoreRevision.isPending} onCancel={() => setPendingRestore(undefined)} onConfirm={() => { if (selectedDocument && pendingRestore) restoreRevision.mutate({ documentId: selectedDocument.id, revision: pendingRestore.revision, expectedVersion: selectedDocument.version }, { onSuccess: () => setPendingRestore(undefined) }); }}/>
    </div>
  );
}
