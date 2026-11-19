import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { resolveServerActions } from "../../services/collectionPresentation";
import TaskActionBar from "./TaskActionBar";

/**
 * 采集任务动作条的交互测试（jsdom + Testing Library）。
 *
 * 与既有的 `renderToStaticMarkup` 快照测试互补：这里验证**点击后的行为**
 * ——原因弹窗、禁用提示、回调入参，而这些是静态 HTML 断言覆盖不到的。
 *
 * 断言只依赖服务端 `allowed_actions` 语义，不根据 status 推断按钮。
 */
describe("TaskActionBar 交互", () => {
  it("只渲染服务端下发的动作，未知动作被忽略", () => {
    render(
      <TaskActionBar
        actions={resolveServerActions(["start", "pause", "republish"])}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "开始采集" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "暂停采集" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "republish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
  });

  it("空动作集合展示说明且不渲染任何按钮", () => {
    render(<TaskActionBar actions={[]} onAction={vi.fn()} />);

    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText("服务端未下发可执行动作")).toBeInTheDocument();
  });

  it("无需原因的动作直接回调，不弹窗", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <TaskActionBar
        actions={resolveServerActions(["start"])}
        onAction={onAction}
      />,
    );

    await user.click(screen.getByRole("button", { name: "开始采集" }));

    expect(onAction).toHaveBeenCalledWith("start", null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("需要原因的动作先弹窗，未填原因时确认按钮禁用", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <TaskActionBar
        actions={resolveServerActions(["pause"])}
        onAction={onAction}
      />,
    );

    await user.click(screen.getByRole("button", { name: "暂停采集" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    const confirm = screen.getByRole("button", { name: "确认提交" });
    expect(confirm).toBeDisabled();

    await user.click(confirm);

    expect(onAction).not.toHaveBeenCalled();
  });

  it("填写原因后回调携带去空白的原因", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <TaskActionBar
        actions={resolveServerActions(["pause"])}
        onAction={onAction}
      />,
    );

    await user.click(screen.getByRole("button", { name: "暂停采集" }));
    await user.type(
      await screen.findByPlaceholderText(/例如：设备电量不足/),
      "  设备电量不足  ",
    );
    await user.click(screen.getByRole("button", { name: "确认提交" }));

    expect(onAction).toHaveBeenCalledWith("pause", "设备电量不足");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("整体禁用原因存在时按钮禁用并展示提示", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <TaskActionBar
        actions={resolveServerActions(["start"])}
        disabledReason="离线时不允许发起任务动作。"
        onAction={onAction}
      />,
    );

    const button = screen.getByRole("button", { name: "开始采集" });
    expect(button).toBeDisabled();

    await user.click(button);
    expect(onAction).not.toHaveBeenCalled();
  });

  it("单个动作的禁用原因只影响该动作", () => {
    render(
      <TaskActionBar
        actions={resolveServerActions(["start", "submit"])}
        getActionDisabledReason={(action) =>
          action === "start" ? "还有未通过的阻塞项。" : null
        }
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "开始采集" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交任务" })).toBeEnabled();
  });
});
