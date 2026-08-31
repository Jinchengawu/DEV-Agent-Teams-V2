import { describe, expect, it } from "vitest";
import { starterTeamTemplate } from "./TeamTemplatesPage";

describe("TeamTemplate 组织权威边界", () => {
  it("四仓骨架不混入 Stage、Provider、Release Member 或凭证", () => {
    const template = starterTeamTemplate("commerce-team", "电商交付团队");
    const payload = JSON.stringify(template);

    expect(template.workcells.map((item) => item.workcell_key)).toEqual([
      "design",
      "frontend",
      "backend",
      "qa",
    ]);
    expect(new Set(template.workcells.map((item) => item.workcell_key)).size).toBe(4);
    expect(template.workcells.every((item) => item.primary_workspace.kind === "git_repository_v1")).toBe(true);
    expect(payload).not.toMatch(/stage|provider|release_member|credential/i);
  });
});
