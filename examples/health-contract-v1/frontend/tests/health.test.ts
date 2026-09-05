import { describe, expect, it } from "vitest";
import { parseHealthResponse } from "../src/health";

describe("health-contract-v1 响应接纳", () => {
  it.each(["ok", "degraded", "unavailable"])("接纳状态 %s", (status) => {
    expect(parseHealthResponse({ status, version: "health-contract-v1" })).toEqual({
      status,
      version: "health-contract-v1",
    });
  });

  it.each([
    null,
    [],
    {},
    { status: "ok" },
    { status: "invalid", version: "health-contract-v1" },
    { status: "ok", version: "stale-contract" },
    { status: "ok", version: "health-contract-v1", unexpected: true },
  ])("拒绝不符合封闭 Schema 的响应 %#", (payload) => {
    expect(() => parseHealthResponse(payload)).toThrow("HEALTH_RESPONSE_INVALID");
  });
});
