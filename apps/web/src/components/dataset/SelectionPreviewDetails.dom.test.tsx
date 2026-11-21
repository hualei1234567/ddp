import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { SelectionPreview } from "../../types/dataset";
import SelectionPreviewDetails from "./SelectionPreviewDetails";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const APPROVED_EPISODE = "22222222-2222-4222-8222-222222222222";
const EXCLUDED_EPISODE = "33333333-3333-4333-8333-333333333333";

const PREVIEW: SelectionPreview = {
  candidate_count: 3,
  selected_count: 1,
  excluded_count: 1,
  duplicated_count: 1,
  split_counts: { train: 1 },
  split_preview_status: "OK",
  exclusion_summary: [
    {
      code: "ANNOTATION_NOT_APPROVED",
      count: 1,
      sample_episode_ids: [EXCLUDED_EPISODE],
    },
  ],
  items_sample: [
    {
      episode_id: APPROVED_EPISODE,
      episode_version: 1,
      split: "train",
      decision: "SELECTED",
      reason: null,
    },
    {
      episode_id: EXCLUDED_EPISODE,
      episode_version: 2,
      split: null,
      decision: "EXCLUDED",
      reason: "标注未通过复核",
    },
  ],
  warnings: [],
  blocking_issues: [],
  preview_sha256: `sha256:${"a".repeat(64)}`,
};

function renderPanel() {
  return render(
    <MemoryRouter>
      <SelectionPreviewDetails projectId={PROJECT} preview={PREVIEW} />
    </MemoryRouter>,
  );
}

describe("SelectionPreviewDetails 排除原因跳转", () => {
  it("排除摘要显示原因、数量与可跳转的 Episode", () => {
    renderPanel();
    expect(screen.getByText(/ANNOTATION_NOT_APPROVED/)).toBeInTheDocument();
    // 同一 Episode 同时出现在排除摘要与逐条样例里，两处都必须是详情页链接。
    const links = screen.getAllByRole("link", { name: EXCLUDED_EPISODE });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute(
        "href",
        `/episodes/${EXCLUDED_EPISODE}?project_id=${PROJECT}`,
      );
    }
  });

  it("逐条样例显示决策与原因，Episode 可回链", () => {
    renderPanel();
    expect(screen.getByText("标注未通过复核")).toBeInTheDocument();
    expect(screen.getByText("EXCLUDED")).toBeInTheDocument();
    expect(screen.getByText("SELECTED")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: APPROVED_EPISODE }),
    ).toHaveAttribute("href", `/episodes/${APPROVED_EPISODE}?project_id=${PROJECT}`);
  });

  it("没有排除与样例时给出空态而不是空白", () => {
    render(
      <MemoryRouter>
        <SelectionPreviewDetails
          projectId={PROJECT}
          preview={{
            ...PREVIEW,
            exclusion_summary: [],
            items_sample: [],
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(/没有排除条目/)).toBeInTheDocument();
    expect(screen.getByText(/没有逐条决策样例/)).toBeInTheDocument();
  });
});
