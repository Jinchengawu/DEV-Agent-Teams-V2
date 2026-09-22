import type { KeyboardEvent, ReactNode } from "react";
import { Drawer } from "antd";
import type { DrawerProps } from "antd";

const selector = "button, a[href], input, select, textarea, [tabindex]";

export function FocusDrawer(props: Omit<DrawerProps, "drawerRender" | "focusable">) {
  return <Drawer {...props} focusable={{ trap: true, focusTriggerAfterClose: true }} drawerRender={(node) => <DrawerFocusTrap>{node}</DrawerFocusTrap>}/>;
}

function isFocusable(element: HTMLElement) {
  if (element.tabIndex < 0 || element.matches(":disabled, input[type=hidden]") || element.closest("[hidden], [inert], [aria-hidden=true]")) return false;
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    const style = getComputedStyle(current);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }
  return true;
}

/** 补齐 Drawer 在浏览器内容末端 Tab 到 BODY 时不会产生 focusin 的边界。 */
export function DrawerFocusTrap({ children, className }: { children: ReactNode; className?: string }) {
  const keepFocusInside = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab" || event.defaultPrevented) return;
    const root = event.currentTarget;
    const active = document.activeElement;
    // React portal 事件也会冒泡；Select 的弹出层仍由原组件管理。
    if (active && !root.contains(active)) return;
    const focusable = Array.from(root.querySelectorAll<HTMLElement>(selector)).filter(isFocusable);
    if (!focusable.length) { event.preventDefault(); root.focus(); return; }
    const first = focusable[0];
    const last = focusable.at(-1)!;
    if (event.shiftKey && (active === first || active === root)) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (active === last || active === root)) {
      event.preventDefault(); first.focus();
    }
  };
  return <div className={className} tabIndex={-1} onKeyDown={keepFocusInside}>{children}</div>;
}
