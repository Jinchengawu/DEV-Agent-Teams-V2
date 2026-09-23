// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ApiProblem } from "../api/client";
import { ErrorState } from "./AsyncState";

it("查询 preflight 只呈现允许的数值，不显示正文或未声明字段", () => {
  render(<ErrorState error={new ApiProblem(422, {
    code: "KNOWLEDGE_QUERY_BUDGET_EXCEEDED", detail: "完整输入超过预算",
    context: { measured_bytes: 42, allowed_bytes: 32, query: "SECRET-SENTINEL", raw_response: "SECRET-SENTINEL", measured: "SECRET-SENTINEL" },
  })}/>);
  expect(screen.getByLabelText("查询预算诊断").textContent).toContain("输入字节：42");
  expect(screen.queryByText(/SECRET-SENTINEL/)).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});
