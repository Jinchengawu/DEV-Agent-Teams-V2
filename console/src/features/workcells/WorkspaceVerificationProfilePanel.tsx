import { useState } from "react";
import { Button, Select } from "antd";
import { ErrorState } from "../../shared/feedback/AsyncState";
import {
  useQualifyVerificationProfile,
  useSetVerificationProfile,
  useVerificationProfiles,
  type WorkspaceBinding,
} from "./api";

export function WorkspaceVerificationProfilePanel({ projectId, workspace, workcellKey }: {
  projectId: string;
  workspace: WorkspaceBinding;
  workcellKey?: string;
}) {
  const catalog = useVerificationProfiles(projectId);
  const save = useSetVerificationProfile(projectId);
  const qualify = useQualifyVerificationProfile(projectId);
  const [selected, setSelected] = useState<string>();
  const profileId = selected ?? workspace.verification_profile_id ?? undefined;
  const profile = catalog.data?.find((item) => item.id === profileId);
  const qualification = workspace.verification_profile;
  const applicable = catalog.data?.filter((item) => !workcellKey || !("workcell_key" in item) || item.workcell_key === workcellKey);
  const error = catalog.error ?? save.error ?? qualify.error;

  return <section className="workspace-verification-profile" aria-label="机器验证方案">
    <label>机器验证方案<Select
      aria-label="选择机器验证方案"
      value={profileId}
      placeholder="请选择产品验证方案"
      loading={catalog.isLoading}
      onChange={setSelected}
      options={applicable?.map((item) => ({ value: item.id, label: item.name }))}
    /></label>
    {profile && <p><code>{profile.commands.map((command) => command.join(" ")).join(" → ")}</code><br/>每条命令超时 {profile.timeout_seconds} 秒；必须实际发现并通过测试。</p>}
    <Button disabled={!profileId || profileId === workspace.verification_profile_id}
      loading={save.isPending}
      onClick={() => profileId && save.mutate({ workspaceId: workspace.id, expectedVersion: workspace.version, profileId })}>保存验证方案</Button>
    <Button disabled={!workspace.verification_profile_id || profileId !== workspace.verification_profile_id || save.isPending}
      loading={qualify.isPending}
      onClick={() => qualify.mutate({ workspaceId: workspace.id, expectedVersion: workspace.version })}>验证工具链</Button>
    <p>{qualification
      ? `已验证工具链：${qualification.tools.map((tool) => tool.version).join("、")}`
      : workspace.verification_profile_error_code ?? "尚未冻结机器验证资格；历史交付仍可查看。"}</p>
    <small>资格检查会冻结工具身份及方案要求的配置，不代表仓库测试已通过。交付执行期间不能修改方案。</small>
    {error && <ErrorState error={error}/>}
  </section>;
}
