import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { datasetApi } from "../../services/datasets";
import DatasetVersionsSection from "./DatasetVersionsSection";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const DATASET = "22222222-2222-4222-8222-222222222222";
const EPISODE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function mockEmptyLists() {
  const empty = Promise.resolve({ items: [], next_cursor: null });
  vi.spyOn(datasetApi, "listVersions").mockReturnValue(empty);
  vi.spyOn(datasetApi, "listDedupPolicies").mockReturnValue(empty);
  vi.spyOn(datasetApi, "listSplitPolicies").mockReturnValue(empty);
  vi.spyOn(datasetApi, "listLeakagePolicies").mockReturnValue(empty);
  vi.spyOn(datasetApi, "listQualityPolicies").mockReturnValue(empty);
}

function renderSection(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <DatasetVersionsSection projectId={PROJECT} datasetId={DATASET} />
      <LocationProbe />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DatasetVersionsSection 接收审核回链", () => {
  it("URL 带 episode_ids 时打开新建版本并预填选择过滤条件", async () => {
    mockEmptyLists();
    renderSection(`/datasets/${DATASET}?episode_ids=${EPISODE}`);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    const textarea = await screen.findByLabelText("选择过滤条件（JSON，可选）");
    await waitFor(() =>
      expect((textarea as HTMLTextAreaElement).value).toBe(
        JSON.stringify({ episode_ids: [EPISODE] }),
      ),
    );
    // 参数被消费，关闭/刷新后不会再次自动弹窗。
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        new RegExp(`^/datasets/${DATASET}$`),
      ),
    );
  });

  it("没有 episode_ids 时不自动弹窗", async () => {
    mockEmptyLists();
    renderSection(`/datasets/${DATASET}`);
    await waitFor(() =>
      expect(datasetApi.listVersions).toHaveBeenCalled(),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
