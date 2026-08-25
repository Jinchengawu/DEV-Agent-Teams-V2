import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Select } from "antd";
import { CheckCircle2, Plus, Save, Upload } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";

type Profile = components["schemas"]["AgentProfile"];
type Draft = components["schemas"]["AgentProfileDraft"];
type Revision = components["schemas"]["AgentProfileRevision"];
type Spec = components["schemas"]["AgentProfileSpec"];
type WithDraft = components["schemas"]["AgentProfileWithDraft"];

const defaultSpec = (): Spec => ({
  schema_version: "1", id: "", name: "", description: "负责前端实现与组件测试", tags: ["development", "frontend"],
  instructions: { template_ref: "prompt://frontend-engineer@1", custom_text: "遵守中文界面、公共 API 和前端架构规范", variables_schema: "schema://agent-prompt-variables@1", examples: [] },
  capabilities: [{ id: "frontend.implementation", version: ">=1,<2" }],
  policies: { tool_policy_ref: "policy://frontend-tools@1", resource_policy_ref: "policy://frontend-resources@1", approval_policy_ref: "policy://candidate-approval@1", memory_policy_ref: "policy://session-isolated@1", delegation_policy_ref: "policy://no-delegation@1" },
  isolation_preference: "shared", extensions: {},
});

export function AgentProfilesPanel() {
  const cache = useQueryClient();
  const profiles = useQuery({ queryKey: ["agent-profiles"], queryFn: () => request<Profile[]>("/v1/agent-profiles") });
  const [selectedId, setSelectedId] = useState<string>();
  const [spec, setSpec] = useState<Spec>(defaultSpec);
  const [publishConfirmation, setPublishConfirmation] = useState(false);
  const selectedDraft = useQuery({ queryKey: ["agent-profile-draft", selectedId], queryFn: () => request<Draft>(`/v1/agent-profiles/${selectedId}/draft`), enabled: Boolean(selectedId) });
  useEffect(() => { if (selectedDraft.data) setSpec(selectedDraft.data.spec); }, [selectedDraft.data]);
  const refresh = async (profileId?: string) => {
    await cache.invalidateQueries({ queryKey: ["agent-profiles"] });
    if (profileId) await cache.invalidateQueries({ queryKey: ["agent-profile-draft", profileId] });
  };
  const create = useMutation({ mutationFn: () => request<WithDraft>("/v1/agent-profiles", { method: "POST", body: JSON.stringify({ spec }) }), onSuccess: async (result) => { setSelectedId(result.profile.id); await refresh(result.profile.id); } });
  const save = useMutation({ mutationFn: () => request<Draft>(`/v1/agent-profiles/${selectedId}/draft`, { method: "PATCH", body: JSON.stringify({ expected_version: selectedDraft.data?.version, spec }) }), onSuccess: async () => refresh(selectedId) });
  const validate = useMutation({ mutationFn: () => request<Draft>(`/v1/agent-profiles/${selectedId}/validate`, { method: "POST", body: JSON.stringify({ expected_version: selectedDraft.data?.version }) }), onSuccess: async () => refresh(selectedId) });
  const publish = useMutation({ mutationFn: () => request<Revision>(`/v1/agent-profiles/${selectedId}/publish`, { method: "POST", body: JSON.stringify({ expected_version: selectedDraft.data?.version }) }), onSuccess: async () => refresh(selectedId) });
  const mutationError = create.error || save.error || validate.error || publish.error;
  const draftError = selectedDraft.error;
  const isDirty = Boolean(selectedId && selectedDraft.data && JSON.stringify(spec) !== JSON.stringify(selectedDraft.data.spec));
  const clearMutationErrors = () => { create.reset(); save.reset(); validate.reset(); publish.reset(); };
  const selectProfile = (profileId: string) => { clearMutationErrors(); setSelectedId(profileId); };
  const startNewProfile = () => { clearMutationErrors(); setSelectedId(undefined); setSpec(defaultSpec()); };
  if (profiles.isLoading) return <section className="panel profiles-panel"><LoadingState label="正在读取智能体角色…"/></section>;
  if (profiles.error) return <section className="panel profiles-panel"><ErrorState error={profiles.error} retry={() => void profiles.refetch()}/></section>;
  const items = profiles.data ?? [];
  return <section className="panel profiles-panel">
    <div className="panel-head"><span>智能体角色</span><small>可复用 AgentProfileSpec · 不包含凭据与运行端点</small></div>
    <div className="profile-workbench"><div className="profile-list">
      {items.map((profile) => <Button type="text" block key={profile.id} className={selectedId === profile.id ? "selected" : ""} onClick={() => selectProfile(profile.id)}><b>{profile.name}</b><small>{profile.id} · 已发布 Revision {profile.latest_revision ?? "无"}</small></Button>)}
      {items.length === 0 && <EmptyState title="尚未创建角色" detail="使用右侧中文表单创建前端、测试、PM 或其他逻辑角色。"/>}
    </div><div className="compact-form profile-editor">
      <h3>{selectedId ? "编辑智能体角色" : "创建智能体角色"}</h3>
      <div className="field-grid"><label>角色 ID<Input value={spec.id} disabled={Boolean(selectedId)} placeholder="例如：frontend-engineer" onChange={(event) => setSpec({ ...spec, id: event.target.value })}/></label><label>角色名称<Input value={spec.name} placeholder="例如：前端开发工程师" onChange={(event) => setSpec({ ...spec, name: event.target.value })}/></label></div>
      <label>角色职责<Input.TextArea value={spec.description} onChange={(event) => setSpec({ ...spec, description: event.target.value })}/></label>
      <label>执行指令<Input.TextArea value={spec.instructions.custom_text} onChange={(event) => setSpec({ ...spec, instructions: { ...spec.instructions, custom_text: event.target.value } })}/></label>
      <div className="field-grid"><label>Capability ID<Input value={spec.capabilities[0]?.id ?? ""} onChange={(event) => setSpec({ ...spec, capabilities: [{ id: event.target.value, version: spec.capabilities[0]?.version ?? ">=1,<2" }] })}/></label><label>版本范围<Input value={spec.capabilities[0]?.version ?? ""} onChange={(event) => setSpec({ ...spec, capabilities: [{ id: spec.capabilities[0]?.id ?? "", version: event.target.value }] })}/></label></div>
      <label>隔离偏好<Select aria-label="隔离偏好" value={spec.isolation_preference} onChange={(value) => setSpec({ ...spec, isolation_preference: value })} options={[{ value: "shared", label: "共享实例、会话隔离" }, { value: "dedicated", label: "独占实例" }]}/></label>
      <p className="field-help">Prompt、工具、资源、审批与 Memory 仅保存版本化策略引用。Runtime Feature 由 Adapter 探测，不能在这里伪造。</p>
      <div className="row-actions">{!selectedId && <Button type="primary" icon={<Plus size={15}/>} disabled={!spec.id.trim() || !spec.name.trim() || create.isPending} onClick={() => create.mutate()}>创建角色草稿</Button>}{selectedId && <><Button icon={<Save size={15}/>} disabled={!selectedDraft.data || !isDirty || save.isPending} onClick={() => save.mutate()}>保存草稿</Button><Button icon={<CheckCircle2 size={15}/>} disabled={!selectedDraft.data || isDirty || validate.isPending} onClick={() => validate.mutate()}>校验当前版本</Button><Button type="primary" icon={<Upload size={15}/>} disabled={isDirty || selectedDraft.data?.validation_status !== "valid" || publish.isPending} onClick={() => setPublishConfirmation(true)}>发布不可变 Revision</Button><Button onClick={startNewProfile}>创建新角色</Button></>}</div>
      {selectedDraft.isLoading && <LoadingState label="正在读取角色草稿…"/>}
      {draftError && <ErrorState error={draftError} retry={() => void selectedDraft.refetch()}/>}
      {selectedDraft.data && <small className="field-help">草稿版本 {selectedDraft.data.version} · 校验状态 {validationLabel(selectedDraft.data.validation_status)}{isDirty ? " · 有未保存修改" : ""}</small>}
      {mutationError && <ErrorState error={mutationError}/>}
    </div></div>
    <ConfirmDialog open={publishConfirmation} title={`发布角色“${spec.name || selectedId || ""}”`} detail="发布后会生成不可变 Agent Profile Revision，并可被 Agent Deployment 和流水线执行快照引用；修改职责或 Capability 必须创建后续 Revision。" confirmLabel="确认发布角色 Revision" pending={publish.isPending} onCancel={() => setPublishConfirmation(false)} onConfirm={() => publish.mutate(undefined, { onSuccess: () => setPublishConfirmation(false) })}/>
  </section>;
}

function validationLabel(status: Draft["validation_status"]) { return status === "valid" ? "已通过" : status === "invalid" ? "未通过" : "待校验"; }
