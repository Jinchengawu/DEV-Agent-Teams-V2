// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { ApiProblem } from "../../shared/api/client";
import { ACTIVE_PROJECT_STORAGE_KEY, assertProjectScope, readActiveProjectId, rememberActiveProjectId } from "./api";

beforeEach(() => window.localStorage.clear());

describe("Project Scope Interface", () => {
  it("允许当前项目记录并拒绝跨项目响应", () => {
    expect(assertProjectScope("pj1", [{ project_id: "pj1", id: "one" }], "交付列表")).toHaveLength(1);
    expect(() => assertProjectScope("pj1", [{ project_id: "pj2", id: "leaked" }], "交付列表")).toThrowError(ApiProblem);
    try { assertProjectScope("pj1", [{ project_id: "pj2" }], "交付列表"); } catch (error) {
      expect((error as ApiProblem).problem.code).toBe("PROJECT_SCOPE_MISMATCH");
    }
  });

  it("在全局页面之间保留用户最后选择的项目", () => {
    expect(readActiveProjectId()).toBeUndefined();
    rememberActiveProjectId("pj-mobile");
    expect(window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY)).toBe("pj-mobile");
    expect(readActiveProjectId()).toBe("pj-mobile");
  });
});
