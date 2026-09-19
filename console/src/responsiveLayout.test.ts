/// <reference types="vite/client" />

// @ts-expect-error Vitest runs this structural CSS contract in Node; the browser bundle never imports it.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const responsiveStyles = [
  new URL("./app/console-theme.css", import.meta.url),
  new URL("./design-system/product.css", import.meta.url),
  new URL("./design-system/compatibility.css", import.meta.url),
].map((path) => readFileSync(path, "utf8")).join("\n");

describe("P1 移动端布局契约", () => {
  it("Agents 新 Tab 类使用局部滚动且不撑宽页面", () => {
    expect(responsiveStyles).toMatch(/\.agent-workspace-tabs-v2[^{}]*\{[^}]*overflow-x:\s*auto/s);
  });

  it("Overview 验证方案允许收缩且 Select 不越界", () => {
    expect(responsiveStyles).toMatch(/\.workspace-verification-profile[^{}]*\{[^}]*min-width:\s*0/s);
    expect(responsiveStyles).toMatch(/\.workspace-verification-profile[^{}]*\.ant-select[^{}]*\{[^}]*width:\s*100%/s);
  });

  it("Orchestration 在窄屏只允许画布局部平移", () => {
    expect(responsiveStyles).toMatch(/@media\s*\(max-width:\s*620px\)[\s\S]*\.dependency-creator[^{}]*\{[^}]*grid-template-columns:\s*1fr/);
    expect(responsiveStyles).toMatch(/\.orchestration-canvas-scroll[^{}]*\{[^}]*overflow:\s*auto/s);
  });

  it("TeamTemplate 浅色选中态使用主题色而不是固定深色底", () => {
    expect(responsiveStyles).toMatch(/\.team-catalog-list\s*>\s*\.ant-btn\.selected[^{}]*\{[^}]*background:\s*var\(--role-surface-selected\)/s);
    expect(responsiveStyles).toMatch(/\.team-catalog-list\s*>\s*\.ant-btn\.selected[^{}]*\{[^}]*color:\s*var\(--role-text-primary\)/s);
  });
});
