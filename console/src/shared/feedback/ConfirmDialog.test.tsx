// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

afterEach(() => { document.body.style.overflow = ""; });

describe("ConfirmDialog", () => {
  test("打开后锁定背景滚动并将焦点限制在对话框", () => {
    render(<ConfirmDialog open title="归档项目" detail="归档后不可恢复" confirmLabel="确认归档" onCancel={() => undefined} onConfirm={() => undefined}/>);
    const cancel = screen.getByRole("button", { name: "取消" });
    const confirm = screen.getByRole("button", { name: "确认归档" });
    expect(document.activeElement).toBe(cancel);
    expect(document.body.style.overflow).toBe("hidden");
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirm);
  });

  test("Escape 取消并在忙碌时阻止关闭", () => {
    const cancel = vi.fn();
    const view = render(<ConfirmDialog open title="删除节点" detail="同时删除关联边" confirmLabel="确认删除" onCancel={cancel} onConfirm={() => undefined}/>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(cancel).toHaveBeenCalledTimes(1);
    view.rerender(<ConfirmDialog open pending title="删除节点" detail="同时删除关联边" confirmLabel="确认删除" onCancel={cancel} onConfirm={() => undefined}/>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
