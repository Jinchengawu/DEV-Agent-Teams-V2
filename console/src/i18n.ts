const statuses: Record<string, string> = {
  queued: "已排队",
  planning: "规划中",
  awaiting_plan_decision: "等待计划审批",
  executing: "执行中",
  verifying: "验证中",
  awaiting_candidate_decision: "等待候选审批",
  applying: "应用中",
  completed: "已完成",
  rejected: "已拒绝",
  failed: "失败",
  cancelled: "已取消",
  unknown: "未知",
  ready: "就绪",
  valid: "校验通过",
  invalid: "校验失败",
  passed: "通过",
};

const commands: Record<string, string> = {
  "approve-plan": "批准计划",
  "reject-plan": "拒绝计划",
  "accept-candidate": "接受候选",
  "reject-candidate": "拒绝候选",
  cancel: "取消交付",
};

const artifactTypes: Record<string, string> = {
  manual: "手工文档",
  requirement: "需求",
  task: "任务合同",
  candidate: "候选变更",
  verification: "机器验证",
  "plan-gate": "计划审批",
  "candidate-gate": "候选审批",
  "apply-receipt": "应用回执",
  "journey-revision": "旅程版本",
};

const journeySteps: Record<string, string> = {
  requirements: "需求分析",
  tasking: "任务规划",
  "approve-plan": "计划审批",
  delivery: "代码交付",
  "approve-candidate": "候选审批",
};

export function statusLabel(value: string): string {
  return statuses[value] ?? value;
}

export function commandLabel(value: string): string {
  return commands[value] ?? value;
}

export function artifactTypeLabel(value: string): string {
  return artifactTypes[value] ?? value;
}

export function documentTitle(title: string, artifactType: string): string {
  const suffix = ` · ${artifactType}`;
  return title.endsWith(suffix)
    ? `${title.slice(0, -suffix.length)} · ${artifactTypeLabel(artifactType)}`
    : title;
}

export function journeyStepLabel(value: string): string {
  return journeySteps[value] ?? value;
}

export function identityLabel(value?: string): string {
  if (!value) return "未获取身份";
  if (value === "codex-simulated-hermes") return "Codex 模拟 Hermes";
  if (value === "codex-cli") return "Codex 命令行";
  if (value === "deterministic-test") return "确定性测试身份";
  return value;
}

export function runtimeTypeLabel(value: string): string {
  if (value === "codex-cli") return "Codex 命令行";
  if (value === "hermes-http") return "Hermes HTTP 服务";
  if (value === "hermes-acp") return "Hermes ACP 服务";
  return value;
}

export function httpErrorLabel(status: number): string {
  if (status === 409) return "当前数据已变化或操作与当前状态冲突，请刷新后重试。";
  if (status === 404) return "未找到请求的数据，请刷新页面后重试。";
  if (status === 422) return "输入内容不符合要求，请检查后重试。";
  if (status === 503) return "运行依赖尚未就绪，请先完成环境检查。";
  return `请求失败，状态码 ${status}。`;
}
