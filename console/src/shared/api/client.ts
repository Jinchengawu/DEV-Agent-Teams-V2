import type { components } from "./generated/schema";

export type ProblemDetail = {
  code?: string;
  title?: string;
  detail?: string;
  repair?: string;
  trace_id?: string;
  expected_version?: number;
  actual_version?: number;
};

export class ApiProblem extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: ProblemDetail,
  ) {
    super(problem.detail ?? problem.title ?? `请求失败（${status}）`);
    this.name = "ApiProblem";
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken = readCookie("agent_team_os_csrf");
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(method !== "GET" && method !== "HEAD" && csrfToken
        ? { "X-CSRF-Token": csrfToken }
        : {}),
      ...init?.headers,
    },
  });
  const contentType = response.headers.get("content-type") ?? "";
  const body: unknown = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const fallback: ProblemDetail = {
      code: `HTTP_${response.status}`,
      title: "操作未完成",
      detail: typeof body === "object" && body !== null && "detail" in body
        ? String(body.detail)
        : `服务返回状态码 ${response.status}。`,
      repair: response.status === 409 ? "刷新当前数据后重试。" : "检查服务状态后重试。",
    };
    throw new ApiProblem(response.status, typeof body === "object" && body !== null ? { ...fallback, ...body } : fallback);
  }
  return body as T;
}

function readCookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

export type Delivery = components["schemas"]["DeliveryRun"];
export type EvidenceRecord = components["schemas"]["EvidenceRecord"];
export type ProductEvent = components["schemas"]["ProductEvent"];
export type AppSettings = components["schemas"]["AppSettings"];
export type AppSettingsPatch = components["schemas"]["AppSettingsPatch"];
export type CurrentUser = components["schemas"]["User"];
