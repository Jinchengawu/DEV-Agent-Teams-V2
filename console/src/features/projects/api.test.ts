// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { ApiProblem } from "../../shared/api/client";
import {
  ACTIVE_PROJECT_STORAGE_KEY,
  assertProjectScope,
  projectIdFromPath,
  readActiveProjectId,
  rememberActiveProjectId,
} from "./api";

beforeEach(() => window.localStorage.clear());

describe("项目切换公共接口", () => {
  it("让外层应用壳从项目作用域 URL 读取当前项目", () => {
    expect(projectIdFromPath("/projects/pj-one/deliveries")).toBe("pj-one");
    expect(projectIdFromPath("/projects/%E9%A1%B9%E7%9B%AE/overview")).toBe("项目");
    expect(projectIdFromPath("/settings")).toBeUndefined();
  });

  it("保留最后选择的项目，供全局页面返回项目工作区时恢复", () => {
    expect(readActiveProjectId()).toBeUndefined();
    rememberActiveProjectId("pj-two");
    expect(window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY)).toBe("pj-two");
    expect(readActiveProjectId()).toBe("pj-two");
  });

  it("拒绝展示混入其他项目的资源", () => {
    expect(assertProjectScope("pj-one", [{ id: "one", project_id: "pj-one" }], "交付列表")).toHaveLength(1);
    expect(() => assertProjectScope("pj-one", [{ id: "leaked", project_id: "pj-two" }], "交付列表")).toThrowError(ApiProblem);
    try {
      assertProjectScope("pj-one", [{ project_id: "pj-two" }], "交付列表");
    } catch (error) {
      expect((error as ApiProblem).problem.code).toBe("PROJECT_SCOPE_MISMATCH");
    }
  });
});
