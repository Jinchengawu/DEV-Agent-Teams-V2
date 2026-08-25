/// <reference types="vite/client" />

import { describe, expect, it } from "vitest";

const productionTsx = import.meta.glob("./**/*.tsx", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

describe("前端组件库边界", () => {
  it("生产页面不直接声明浏览器原生交互控件", () => {
    const violations = Object.entries(productionTsx)
      .filter(([path]) => !path.endsWith(".test.tsx"))
      .flatMap(([path, source]) => {
        const matches = [...source.matchAll(/<(?:button|input|select|textarea|option|optgroup|details|summary)(?:\s|>)/g)];
        return matches.map((match) => `${path}:${source.slice(0, match.index).split("\n").length}`);
      });

    expect(violations, "交互控件必须由 Ant Design 或项目 UI 适配器提供").toEqual([]);
  });
});
