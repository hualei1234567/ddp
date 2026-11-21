import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  EPISODE_ID,
  PROJECT_ID,
  annotationSubmissionFixture,
} from "../../services/__fixtures__/annotationFixtures";
import { datasetApi } from "../../services/datasets";
import type { Dataset } from "../../types/dataset";
import DatasetLinkagePanel from "./DatasetLinkagePanel";

const DATASET_ID = "12345678-1234-4234-8234-123456789012";

const datasetFixture: Dataset = {
  dataset_id: DATASET_ID,
  project_id: PROJECT_ID,
  dataset_lineage_id: "87654321-4321-4321-8321-210987654321",
  code: "DS-E2E",
  name: "家庭操作训练集",
  description: null,
  purpose: "TRAINING",
  owner_id: "541eb26e-9979-459a-9c2f-7dfe174f1778",
  status: "ACTIVE",
  current_version_id: null,
  tags: [],
  usage_policy: "TRAINING_ALLOWED",
  frozen_version_count: 0,
  allowed_actions: [],
  version: 1,
  created_at: "2026-11-22T00:00:00Z",
  updated_at: "2026-11-22T00:00:00Z",
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderPanel(status: typeof annotationSubmissionFixture.status) {
  const submission = { ...annotationSubmissionFixture, status };
  return render(
    <MemoryRouter>
      <DatasetLinkagePanel
        projectId={PROJECT_ID}
        submission={submission}
        records={submission.records}
      />
      <LocationProbe />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DatasetLinkagePanel 审核 → 数据集闭环", () => {
  it("未通过的提交不能加入数据集，并说明原因", () => {
    renderPanel("SUBMITTED");
    expect(screen.getByRole("button", { name: /加入数据集/ })).toBeDisabled();
    expect(screen.getByText("复核通过后才能加入数据集")).toBeInTheDocument();
  });

  it("Episode 回链到详情页", () => {
    renderPanel("APPROVED");
    const link = screen.getByRole("link", { name: EPISODE_ID });
    expect(link).toHaveAttribute(
      "href",
      `/episodes/${EPISODE_ID}?project_id=${PROJECT_ID}`,
    );
  });

  it("审核通过后选数据集，跳转到带 Episode 过滤的构建入口", async () => {
    const list = vi
      .spyOn(datasetApi, "list")
      .mockResolvedValue({ items: [datasetFixture], next_cursor: null });
    renderPanel("APPROVED");

    await userEvent.click(screen.getByRole("button", { name: /加入数据集/ }));
    await waitFor(() => expect(list).toHaveBeenCalledWith(PROJECT_ID, expect.anything()));

    await userEvent.click(await screen.findByRole("combobox", { name: "目标数据集" }));
    await userEvent.click(await screen.findByText(/家庭操作训练集/));
    await userEvent.click(screen.getByRole("button", { name: /确认加入/ }));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/datasets/${DATASET_ID}?episode_ids=${EPISODE_ID}`,
      ),
    );
  });
});
