import { describe, expect, it } from "vitest";
import { repositoryIsolationSummary } from "./ProjectWorkcellSetup";

describe("四仓隔离投影", () => {
  it("只在四个 Workcell 使用四个不同 Repository 时标记隔离", () => {
    expect(repositoryIsolationSummary(["design.git", "frontend.git", "backend.git", "qa.git"])).toEqual({ repositoryCount: 4, uniqueRepositoryCount: 4, isolated: true });
    expect(repositoryIsolationSummary(["shared.git", "shared.git"])).toEqual({ repositoryCount: 2, uniqueRepositoryCount: 1, isolated: false });
  });
});
