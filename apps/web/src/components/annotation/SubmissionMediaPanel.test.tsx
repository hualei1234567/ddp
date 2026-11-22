// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnnotationRecord } from "../../types/annotation";
import { episodeFixture } from "../../services/__fixtures__/collectionFixtures";
import SubmissionMediaPanel from "./SubmissionMediaPanel";

// 本文件的用例要等"Episode → 血缘 → 资产 → 签名地址"多段串行请求结算；
// 默认 1s 在满载机器上不够，等待上限单独提出来，避免每个断言各写一个魔法数。
const ASYNC_CHAIN_TIMEOUT_MS = 5_000;

const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const EPISODE_ID = "33333333-3333-3333-3333-333333333333";
const ASSET_ID = "44444444-4444-4444-4444-444444444444";

const getEpisode = vi.fn();
const getEpisodeLineage = vi.fn();
const getAsset = vi.fn();
const getDownloadUrl = vi.fn();

vi.mock("../../services/collections", () => ({
  collectionsApi: {
    getEpisode: (...args: unknown[]) => getEpisode(...args),
  },
}));
vi.mock("../../services/lineage", () => ({
  lineageApi: {
    getEpisodeLineage: (...args: unknown[]) => getEpisodeLineage(...args),
  },
}));
vi.mock("../../services/rawAssets", () => ({
  rawAssetsApi: {
    getAsset: (...args: unknown[]) => getAsset(...args),
    getDownloadUrl: (...args: unknown[]) => getDownloadUrl(...args),
  },
}));

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

function record(overrides: Partial<AnnotationRecord> = {}): AnnotationRecord {
  return {
    annotation_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    annotation_lineage_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    annotation_version: 1,
    project_id: PROJECT_ID,
    submission_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    episode_id: EPISODE_ID,
    episode_version: 1,
    task_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    schema_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    schema_version: 1,
    status: "DRAFT",
    time_range: { start_time_ns: "1000000000", end_time_ns: "4000000000" },
    labels: [
      {
        label_key: "grasp_segment",
        value: { start_time_ns: 1000000000, end_time_ns: 3000000000 },
        source: "HUMAN",
        label_status: "CONFIRMED",
      },
    ],
    evidence_refs: [],
    record_sha256: "f".repeat(64),
    supersedes_id: null,
    approved_by: null,
    approved_at: null,
    version: 1,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: null,
    created_by: null,
    updated_by: null,
    allowed_actions: [],
    ...overrides,
  } as AnnotationRecord;
}

function playableAsset() {
  return {
    asset_id: ASSET_ID,
    project_id: PROJECT_ID,
    code: "asset-001",
    source_type: "UPLOAD",
    device_time_raw: null,
    collection_task_id: null,
    batch_id: null,
    upload_session_id: null,
    original_filename: "cam-front.mp4",
    object_key: "project/cam-front.mp4",
    size_bytes: 4096,
    content_type: "video/mp4",
    format: "MP4",
    sha256: "a".repeat(64),
    status: "VALID",
    captured_at: null,
    ingested_at: null,
    device_ids: [],
    sensor_ids: [],
    sensor_snapshot: null,
    raw_metadata: {},
    storage_class: "STANDARD",
    retention_policy_id: null,
    quarantine_reason: null,
    quarantine_codes: [],
    scan_report_key: null,
    verified_at: null,
    archived_at: null,
    tombstoned_at: null,
    duplicate_of_asset_id: null,
    uploaded_by: "77777777-7777-7777-7777-777777777777",
    version: 1,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: null,
    created_by: null,
    updated_by: null,
    allowed_actions: [],
  };
}

function episodeWithRange() {
  return {
    ...episodeFixture,
    episode_id: EPISODE_ID,
    project_id: PROJECT_ID,
    start_time_ns: "0",
    end_time_ns: "10000000000",
  };
}

describe("SubmissionMediaPanel", () => {
  beforeEach(() => {
    getEpisode.mockReset();
    getEpisodeLineage.mockReset();
    getAsset.mockReset();
    getDownloadUrl.mockReset();
  });

  it("记录 → Episode → 反向血缘 → 播放器：真实串起媒体证据", async () => {
    getEpisode.mockResolvedValue(episodeWithRange());
    getEpisodeLineage.mockResolvedValue({
      direction: "reverse",
      items: [
        {
          edge_id: "eeeeeeee-1111-1111-1111-111111111111",
          project_id: PROJECT_ID,
          from_node_type: "RAW_ASSET",
          from_node_id: ASSET_ID,
          to_node_type: "EPISODE",
          to_node_id: EPISODE_ID,
          relation: "SEGMENTED_INTO",
          metadata: {},
          created_by: "77777777-7777-7777-7777-777777777777",
          created_at: "2026-11-20T10:00:00Z",
        },
      ],
    });
    getAsset.mockResolvedValue(playableAsset());
    getDownloadUrl.mockResolvedValue({
      url: "https://storage.test/signed/cam-front.mp4",
      expires_at: "2026-11-22T10:00:00Z",
    });

    render(
      <SubmissionMediaPanel projectId={PROJECT_ID} records={[record()]} />,
    );

    // 这条断言要等 Episode → 反向血缘 → 资产 → 签名地址四段串行请求依次结算，
    // 机器满载时 React Testing Library 默认的 1s 上限不够（本地全量单测曾偶发超时）。
    // 放宽的只是等待上限，断言本身不变：仍然必须出现可播放的 video 元素。
    expect(
      await screen.findByTestId("media-preview-video", undefined, {
        timeout: ASYNC_CHAIN_TIMEOUT_MS,
      }),
    ).toBeInTheDocument();
    expect(getEpisode).toHaveBeenCalledWith(
      PROJECT_ID,
      EPISODE_ID,
      expect.anything(),
    );
    expect(getEpisodeLineage).toHaveBeenCalledWith(
      PROJECT_ID,
      EPISODE_ID,
      expect.objectContaining({ direction: "reverse" }),
    );
    expect(getAsset).toHaveBeenCalledWith(
      PROJECT_ID,
      ASSET_ID,
      expect.anything(),
    );
    // 记录起点是 1s，时间轴播放头应定位到该记录起点而非 0。
    // 元素出现不等于定位已完成：播放头在后续 effect 里更新，必须等属性就位。
    const timeline = await screen.findByTestId("annotation-timeline", undefined, {
      timeout: ASYNC_CHAIN_TIMEOUT_MS,
    });
    await waitFor(() => {
      expect(timeline.getAttribute("aria-valuenow")).toBe("1");
    });
    // 标签片段（1s–3s）必须渲染到时间轴上，供复核定位。
    expect(screen.getByTitle(/grasp_segment/)).toBeInTheDocument();
  });

  it("Episode 读取失败时明确提示，不伪装成无媒体", async () => {
    getEpisode.mockRejectedValue(new Error("forbidden"));

    render(
      <SubmissionMediaPanel projectId={PROJECT_ID} records={[record()]} />,
    );

    expect(await screen.findByText(/Episode 读取失败/)).toBeInTheDocument();
    expect(screen.queryByTestId("media-preview-video")).not.toBeInTheDocument();
  });

  it("没有切分来源时如实说明，而不是渲染空播放器", async () => {
    getEpisode.mockResolvedValue(episodeWithRange());
    getEpisodeLineage.mockResolvedValue({ direction: "reverse", items: [] });

    render(
      <SubmissionMediaPanel projectId={PROJECT_ID} records={[record()]} />,
    );

    expect(await screen.findByText(/尚未关联上游媒体/)).toBeInTheDocument();
    expect(getAsset).not.toHaveBeenCalled();
  });

  it("多条记录可切换，切换后重新定位播放头", async () => {
    const second = record({
      annotation_id: "bbbbbbbb-1111-1111-1111-111111111111",
      time_range: { start_time_ns: "6000000000", end_time_ns: "8000000000" },
      labels: [],
    });
    getEpisode.mockResolvedValue(episodeWithRange());
    getEpisodeLineage.mockResolvedValue({ direction: "reverse", items: [] });

    render(
      <SubmissionMediaPanel
        projectId={PROJECT_ID}
        records={[record(), second]}
      />,
    );

    const timeline = await screen.findByTestId("annotation-timeline");
    expect(timeline.getAttribute("aria-valuenow")).toBe("1");

    await userEvent.click(
      screen.getByRole("combobox", { name: "选择标注记录" }),
    );
    await userEvent.click(await screen.findByText("记录 2（v1）"));

    await waitFor(() => {
      expect(
        screen.getByTestId("annotation-timeline").getAttribute("aria-valuenow"),
      ).toBe("6");
    });
  });
});
