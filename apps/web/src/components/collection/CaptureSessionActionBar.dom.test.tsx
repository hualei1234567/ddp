import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import CaptureSessionActionBar from "./CaptureSessionActionBar";

describe("CaptureSessionActionBar", () => {
  it("只渲染 allowed_actions 中五种白名单动作", () => {
    render(<CaptureSessionActionBar allowedActions={["start", "delete", "abort"]} onAction={vi.fn()} />);
    expect(screen.getByRole("button", { name: "开始采集" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "中止会话" })).toBeEnabled();
    expect(screen.queryByText("delete")).toBeNull();
  });

  it("空数组是无动作终态", () => {
    render(<CaptureSessionActionBar allowedActions={[]} onAction={vi.fn()} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText(/当前会话没有可执行动作/)).toBeInTheDocument();
  });

  it.each(["pause", "resume", "abort"] as const)("%s 必填原因并提交去空白值", async (action) => {
    const user = userEvent.setup(); const onAction = vi.fn().mockResolvedValue(undefined);
    render(<CaptureSessionActionBar allowedActions={[action]} onAction={onAction} />);
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button", { name: "确认提交" })).toBeDisabled();
    await user.type(screen.getByLabelText("动作原因"), "  现场原因  ");
    await user.click(screen.getByRole("button", { name: "确认提交" }));
    expect(onAction).toHaveBeenCalledWith(action, "现场原因");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
