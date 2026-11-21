// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { LabelDefinition } from "../../types/annotation";
import ObjectTrackEditor from "./ObjectTrackEditor";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

function definition(): LabelDefinition {
  return {
    label_key: "bbox",
    label_type: "OBJECT_TRACK",
    level: "OBJECT_TRACK",
    required: true,
    visibility: "ANNOTATOR",
    description: "跟踪框",
    value_spec: {},
    quality_rules: [],
  };
}

describe("ObjectTrackEditor", () => {
  it("空值时给出空态与新增入口", () => {
    render(
      <ObjectTrackEditor
        definition={definition()}
        value=""
        disabled={false}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText(/还没有轨迹行/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新增轨迹/ })).toBeInTheDocument();
  });

  it("新增行并填满起止时间后写回合法 JSON", async () => {
    const onChange = vi.fn();
    render(
      <ObjectTrackEditor
        definition={definition()}
        value=""
        disabled={false}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /新增轨迹/ }));
    const startInputs = screen.getAllByLabelText(/开始时间/);
    const endInputs = screen.getAllByLabelText(/结束时间/);
    await userEvent.type(startInputs[0]!, "0");
    await userEvent.type(endInputs[0]!, "1000");

    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last).toBeTruthy();
    expect(JSON.parse(last)).toEqual([
      { start_time_ns: 0, end_time_ns: 1000 },
    ]);
  });

  it("时间轴选区可用于新增轨迹", async () => {
    const onChange = vi.fn();
    render(
      <ObjectTrackEditor
        definition={definition()}
        value=""
        disabled={false}
        onChange={onChange}
        pendingRangeNs={{ startNs: 500, endNs: 1500 }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /新增轨迹/ }));

    expect(JSON.parse(onChange.mock.calls.at(-1)?.[0])).toEqual([
      { start_time_ns: 500, end_time_ns: 1500 },
    ]);
  });

  it("非法行不写回草稿，并给出原因", async () => {
    const onChange = vi.fn();
    render(
      <ObjectTrackEditor
        definition={definition()}
        value=""
        disabled={false}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /新增轨迹/ }));
    await userEvent.type(screen.getAllByLabelText(/开始时间/)[0]!, "10");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/必须是整数纳秒/)).toBeInTheDocument();
  });

  it("无法结构化的历史值退回原文，不丢数据", () => {
    const value = '[{"foo": 1}]';
    render(
      <ObjectTrackEditor
        definition={definition()}
        value={value}
        disabled={false}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText(/现有值无法结构化编辑/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue(value);
  });

  it("删除最后一行时写回空字符串", async () => {
    const onChange = vi.fn();
    render(
      <ObjectTrackEditor
        definition={definition()}
        value='[{"start_time_ns":0,"end_time_ns":1000}]'
        disabled={false}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /删除轨迹/ }));

    expect(onChange).toHaveBeenLastCalledWith("");
  });
});
