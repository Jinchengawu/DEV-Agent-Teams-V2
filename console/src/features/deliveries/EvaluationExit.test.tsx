// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EvaluationExit } from "./EvaluationExit";

describe("评测退出", () => {
  it("完成引导后仍需显式确认才调用取消", async () => {
    const cancel = vi.fn();
    render(<MemoryRouter><EvaluationExit projectId="p" purpose="onboarding_evaluation" status="awaiting_candidate_decision" ready boundEvaluation canCancel onCancel={cancel}/></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: "结束评测并释放项目" }));
    expect(cancel).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认结束评测" }));
    expect(cancel).toHaveBeenCalledOnce();
  });
  it.each(["applying", "needs_attention"] as const)("%s 不提供普通取消", (status) => {
    render(<MemoryRouter><EvaluationExit projectId="p" purpose="onboarding_evaluation" status={status} ready boundEvaluation canCancel onCancel={vi.fn()}/></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "结束评测并释放项目" })).toBeNull();
  });
  it("Viewer 无写入口，取消终态才给正式交付链接", () => {
    const props = { projectId: "p", purpose: "onboarding_evaluation" as const, ready: true, boundEvaluation: true, canCancel: false, onCancel: vi.fn() };
    const view = render(<MemoryRouter><EvaluationExit {...props} status="awaiting_candidate_decision"/></MemoryRouter>);
    expect(screen.queryByRole("button")).toBeNull();
    view.rerender(<MemoryRouter><EvaluationExit {...props} canCancel status="cancelled"/></MemoryRouter>);
    expect(screen.getByRole("link", { name: "发起正式交付" }).getAttribute("href")).toBe("/projects/p/deliveries");
  });
  it.each(["product", "not-ready", "other-evaluation"])("%s 不出现评测退出", (condition) => {
    render(<MemoryRouter><EvaluationExit projectId="p" purpose={condition === "product" ? "product" : "onboarding_evaluation"} status="awaiting_candidate_decision" ready={condition !== "not-ready"} boundEvaluation={condition !== "other-evaluation"} canCancel onCancel={vi.fn()}/></MemoryRouter>);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("失败保留原评测上下文和重试入口", () => {
    render(<MemoryRouter><EvaluationExit projectId="p" purpose="onboarding_evaluation" status="awaiting_candidate_decision" ready boundEvaluation canCancel error={new Error("版本已变化，请刷新后重试")} onCancel={vi.fn()}/></MemoryRouter>);
    expect(screen.getByText("引导已完成，当前评测仍占用项目")).toBeTruthy();
    expect(screen.getByRole("button", { name: "结束评测并释放项目" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "发起正式交付" })).toBeNull();
  });
  it("清理中保持等待，不提前进入正式交付", () => {
    render(<MemoryRouter><EvaluationExit projectId="p" purpose="onboarding_evaluation" status="cancelling" ready boundEvaluation canCancel onCancel={vi.fn()}/></MemoryRouter>);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "发起正式交付" })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
