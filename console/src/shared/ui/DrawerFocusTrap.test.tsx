// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DrawerFocusTrap } from "./DrawerFocusTrap";

describe("DrawerFocusTrap", () => {
  it("双向循环并跳过 disabled hidden inert 和负 tabIndex", () => {
    render(<DrawerFocusTrap><button>首项</button><button disabled>禁用</button><button tabIndex={-1}>负序</button><div hidden><button>隐藏</button></div><div inert><button>惰性</button></div><div style={{ display: "none" }}><button>不可见</button></div><button>末项</button></DrawerFocusTrap>);
    const first = screen.getByText("首项");
    const last = screen.getByText("末项");
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(last, { key: "Escape" });
    expect(document.activeElement).toBe(last);
  });

  it("无可聚焦内容时将焦点留在容器", () => {
    const { container } = render(<DrawerFocusTrap><span>空抽屉</span></DrawerFocusTrap>);
    const trap = container.firstElementChild as HTMLElement;
    trap.focus();
    fireEvent.keyDown(trap, { key: "Tab" });
    expect(document.activeElement).toBe(trap);
  });
});
