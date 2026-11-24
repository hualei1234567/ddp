import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ConflictContext } from "../../services/annotationConflict";
import { EPISODE_ID } from "../../services/__fixtures__/annotationFixtures";
import { episodeFixture } from "../../services/__fixtures__/collectionFixtures";
import type { EpisodeFormValues } from "../../services/annotationValidation";
import AnnotationConflictModal from "./AnnotationConflictModal";

/**
 * FR-019 冲突恢复对话框交互测试。
 *
 * 语义红线（审计结论）：审计明确「『复制草稿/人工合并』的完整恢复流未做」，
 * 而恢复流的每一步都必须是**用户显式操作**。这里逐条锁死：
 * 1. 提示必须写出「当前版本 N / 你基于版本 M」，且声明输入未丢；
 * 2. 「复制草稿」必须触发剪贴板，并可回退为下载——不是静默失败；
 * 3. 逐项合并：未选择的字段保留我的，选服务端的才替换；
 * 4. 服务端内容不可比时**不伪造**差异表，只允许复制/刷新；
 * 5. 关闭对话框不触发任何提交（不说成"已解决冲突"）。
 */
const mine: Record<string, EpisodeFormValues> = {
  [EPISODE_ID]: {
    action: "PLACE",
    confidence: "0.75",
    segment: { start: "1200", end: "1800" },
  },
};

function context(overrides: Partial<ConflictContext> = {}): ConflictContext {
  return {
    expectedVersion: 3,
    currentVersion: 7,
    serverValues: { [EPISODE_ID]: { action: "PICK" } },
    ...overrides,
  };
}

function renderModal(
  overrides: Partial<Parameters<typeof AnnotationConflictModal>[0]> = {},
) {
  const onApplyMerged = vi.fn();
  const onCopyDraft = vi.fn().mockResolvedValue(true);
  const onRefreshFromServer = vi.fn();
  const onDismiss = vi.fn();
  render(
    <AnnotationConflictModal
      open
      context={context()}
      mine={mine}
      episodes={[episodeFixture]}
      busy={false}
      onCopyDraft={onCopyDraft}
      onApplyMerged={onApplyMerged}
      onRefreshFromServer={onRefreshFromServer}
      onDismiss={onDismiss}
      {...overrides}
    />,
  );
  return { onApplyMerged, onCopyDraft, onRefreshFromServer, onDismiss };
}

describe("AnnotationConflictModal（FR-019 冲突恢复）", () => {
  it("标题与提示写明「当前版本 N、你基于版本 M」，并声明输入未丢失", () => {
    renderModal();
    expect(
      screen.getByText(/他人已更新此标注（当前版本 7，你基于版本 3）/),
    ).toBeInTheDocument();
    expect(screen.getByText("你的输入没有被丢弃")).toBeInTheDocument();
    expect(screen.getByText(/关闭本对话框或刷新页面都不会丢失/)).toBeInTheDocument();
  });

  it("逐项列出差异：服务端值、我的值、差异类型都能看到", () => {
    renderModal();
    // 3 项差异：action 值不同、confidence 仅我有、segment 仅我有
    // （服务端版本只有 action 一个标签）。
    expect(screen.getByText(/共 3 项差异/)).toBeInTheDocument();
    expect(screen.getByText("值不同")).toBeInTheDocument();
    expect(screen.getByText(/仅我有/)).toBeInTheDocument();
    // 我的值与服务端值并列展示，用户能肉眼比对。
    expect(screen.getByText("PLACE")).toBeInTheDocument();
    expect(screen.getAllByText("PICK").length).toBeGreaterThan(0);
  });

  it("复制草稿调用剪贴板；剪贴板不可用时提示已降级为下载", async () => {
    const user = userEvent.setup();
    // 剪贴板不可用：onCopyDraft 返回 false（调用方已降级写文件）。
    const onCopyDraft = vi.fn().mockResolvedValue(false);
    renderModal({ onCopyDraft });
    await user.click(screen.getByRole("button", { name: "复制草稿" }));
    await waitFor(() => expect(onCopyDraft).toHaveBeenCalledWith("clipboard"));
    expect(
      await screen.findByText(/剪贴板不可用，草稿已作为 JSON 文件下载/),
    ).toBeInTheDocument();
  });

  it("「下载 JSON」走下载路径，与复制草稿等价", async () => {
    const user = userEvent.setup();
    const { onCopyDraft } = renderModal();
    await user.click(screen.getByRole("button", { name: "下载 JSON" }));
    await waitFor(() => expect(onCopyDraft).toHaveBeenCalledWith("download"));
    expect(await screen.findByText(/草稿已下载为 JSON 文件/)).toBeInTheDocument();
  });

  it("默认保留我的值；逐项选「采用服务端」后才替换，并回传合并结果", async () => {
    const user = userEvent.setup();
    const { onApplyMerged } = renderModal();

    // 先不改选择直接确认：合并结果应保留我的 PLACE。
    await user.click(
      screen.getByRole("button", { name: "用合并结果重新提交" }),
    );
    await waitFor(() => expect(onApplyMerged).toHaveBeenCalledTimes(1));
    const untouched = onApplyMerged.mock.calls[0]![0] as Record<
      string,
      EpisodeFormValues
    >;
    expect(untouched[EPISODE_ID]?.action).toBe("PLACE");
    expect(untouched[EPISODE_ID]?.confidence).toBe("0.75");
  });

  it("点某行的「采用服务端」只影响该字段", async () => {
    const user = userEvent.setup();
    const { onApplyMerged } = renderModal();

    // action 行是"值不同"那一行，采用服务端 → PICK。
    const serverButtons = screen.getAllByRole("button", {
      name: "采用服务端",
    });
    await user.click(serverButtons[0]!);
    await user.click(
      screen.getByRole("button", { name: "用合并结果重新提交" }),
    );
    await waitFor(() => expect(onApplyMerged).toHaveBeenCalledTimes(1));
    const merged = onApplyMerged.mock.calls[0]![0] as Record<
      string,
      EpisodeFormValues
    >;
    expect(merged[EPISODE_ID]?.action).toBe("PICK");
    // 未选中的 confidence 仍是我的。
    expect(merged[EPISODE_ID]?.confidence).toBe("0.75");
  });

  it("服务端内容不可比时不展示差异表，且禁用合并提交（不伪造成功）", () => {
    renderModal({
      context: context({ serverValues: null }),
      mine,
    });
    expect(screen.getByText("无法展示逐项差异")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "采用服务端" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "用合并结果重新提交" }),
    ).toBeDisabled();
    // 复制草稿仍然可用：这才是"不丢数据"的保底出口。
    expect(screen.getByRole("button", { name: "复制草稿" })).toBeEnabled();
  });

  it("放弃合并与关闭都只是回调，不自动提交任何内容", async () => {
    const user = userEvent.setup();
    const { onRefreshFromServer, onApplyMerged } = renderModal();
    await user.click(
      screen.getByRole("button", { name: "放弃合并并刷新" }),
    );
    expect(onRefreshFromServer).toHaveBeenCalledTimes(1);
    expect(onApplyMerged).not.toHaveBeenCalled();
  });

  it("刷新后恢复的合并选择会被继续沿用", async () => {
    const user = userEvent.setup();
    const { onApplyMerged } = renderModal({
      initialChoices: { [`${EPISODE_ID}.action`]: "server" },
    });
    await user.click(
      screen.getByRole("button", { name: "用合并结果重新提交" }),
    );
    await waitFor(() => expect(onApplyMerged).toHaveBeenCalledTimes(1));
    const merged = onApplyMerged.mock.calls[0]![0] as Record<
      string,
      EpisodeFormValues
    >;
    expect(merged[EPISODE_ID]?.action).toBe("PICK");
  });
});
