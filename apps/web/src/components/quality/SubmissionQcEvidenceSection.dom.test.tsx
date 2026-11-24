// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnnotationSubmission, ReviewIssue } from "../../types/annotation";
import {
  qualityRunFixture,
  qualityRunFindingFixture,
} from "../../services/__fixtures__/qualityRunFixtures";
import SubmissionQcEvidenceSection from "./SubmissionQcEvidenceSection";

/**
 * V51-010 审核页 QC 证据与返工依据测试。
 *
 * 红线（蓝图 §5）：QC 自动规则提供证据和建议，**不自动代替 Reviewer**。
 * 因此这里重点验证：
 * 1. 只在既有状态机可达的状态下才允许点"创建返工"；
 * 2. 返工原因与 issues 只附带证据事实，不改变状态机语义；
 * 3. 读不到证据时如实报错，不渲染成"没有质量问题"。
 */

const listRuns = vi.fn();
const listFindings = vi.fn();

vi.mock("../../services/qualityRuns", () => ({
  qualityRunApi: {
    listRuns: (...args: unknown[]) => listRuns(...args),
    listFindings: (...args: unknown[]) => listFindings(...args),
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

beforeEach(() => {
  listRuns.mockReset();
  listFindings.mockReset();
});

const PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const EPISODE_ID = "33333333-3333-4333-8333-333333333333";
const EPISODE_ID_2 = "33333333-3333-4333-8333-333333333344";

const RUN = qualityRunFixture({ subject_type: "EPISODE", subject_id: EPISODE_ID });

const FAIL_FINDING = qualityRunFindingFixture({
  finding_id: "11111111-1111-4111-8111-111111111111",
  run_id: RUN.run_id,
  rule_id: "stream_sync_aligned",
  rule_version: "1.1.0",
  category: "TEMPORAL",
  severity: "ERROR",
  status: "FAIL",
  message: "1 个片段同步未通过",
  score: 0,
  evidence_location: {
    time_range: {
      start_time_ns: "1730000003000000000",
      end_time_ns: "1730000006000000000",
    },
    stream_id: "wrist",
    field_path: "streams",
  },
});

const PASS_FINDING = qualityRunFindingFixture({
  finding_id: "22222222-2222-4222-8222-222222222222",
  run_id: RUN.run_id,
  rule_id: "assets_present",
  status: "PASS",
  evidence_location: null,
});

function submission(
  overrides: Partial<AnnotationSubmission> = {},
): AnnotationSubmission {
  return {
    submission_id: "44444444-4444-4444-8444-444444444444",
    project_id: PROJECT_ID,
    task_id: "55555555-5555-4555-8555-555555555555",
    batch_id: "66666666-6666-4666-8666-666666666666",
    assignment_id: "77777777-7777-4777-8777-777777777777",
    annotator_id: "88888888-8888-4888-8888-888888888888",
    submission_version: 1,
    schema_id: "99999999-9999-4999-8999-999999999999",
    schema_version: 1,
    schema_sha256: "sha256:" + "a".repeat(64),
    records_digest: "sha256:" + "b".repeat(64),
    item_count: 1,
    status: "IN_REVIEW",
    data_source: "REAL",
    records: [],
    record_refs: [],
    review_id: null,
    arbitration_id: null,
    supersedes_id: null,
    submitted_at: null,
    decided_at: null,
    reason: null,
    version: 3,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
    ...overrides,
  } as AnnotationSubmission;
}

function renderSection(
  overrides: Partial<Parameters<typeof SubmissionQcEvidenceSection>[0]> = {},
) {
  const props: Parameters<typeof SubmissionQcEvidenceSection>[0] = {
    projectId: PROJECT_ID,
    submission: submission(),
    episodeIds: [EPISODE_ID],
    onRequireRework: () => undefined,
    busy: false,
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <SubmissionQcEvidenceSection {...props} />
    </MemoryRouter>,
  );
}

describe("审核页 QC 证据与返工依据", () => {
  it("列出跨 Episode 的未通过结论，不列通过项", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({
      items: [FAIL_FINDING, PASS_FINDING],
      next_cursor: null,
    });

    renderSection();

    expect(await screen.findByText("stream_sync_aligned")).toBeInTheDocument();
    expect(screen.queryByText("assets_present")).not.toBeInTheDocument();
  });

  it("多个 Episode 各自最近一次运行都会被读取", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({ items: [FAIL_FINDING], next_cursor: null });

    renderSection({ episodeIds: [EPISODE_ID, EPISODE_ID_2] });

    // 两个 Episode 各有一条未通过结论：按 findAllByText 断言，避免歧义。
    await waitFor(() => {
      expect(screen.getAllByText("stream_sync_aligned")).toHaveLength(2);
    });
    expect(listRuns).toHaveBeenCalledTimes(2);
    expect(listFindings).toHaveBeenCalledTimes(2);
  });

  it("提交状态不在 require-rework 可达范围时按钮置灰并说明原因", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({ items: [FAIL_FINDING], next_cursor: null });

    renderSection({ submission: submission({ status: "IN_REVIEW" }) });
    await screen.findByText("stream_sync_aligned");
    await userEvent.click(screen.getByRole("button", { name: /选为返工依据/ }));

    expect(
      screen.getByRole("button", { name: /创建返工（附带 QC 证据）/ }),
    ).toBeDisabled();
    expect(
      screen.getByText(/require-rework` 仅在/),
    ).toBeInTheDocument();
  });

  it("REJECTED 状态下可创建返工，原因附规则版本与证据位置", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({ items: [FAIL_FINDING], next_cursor: null });
    const onRequireRework = vi.fn<(...args: never[]) => void>();

    renderSection({
      submission: submission({ status: "REJECTED" }),
      onRequireRework: onRequireRework as unknown as (
        input: { reason: string; issues: ReviewIssue[] },
      ) => void,
    });
    await screen.findByText("stream_sync_aligned");
    await userEvent.click(screen.getByRole("button", { name: /选为返工依据/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /创建返工（附带 QC 证据）/ }),
    );

    expect(onRequireRework).toHaveBeenCalledTimes(1);
    const input = (
      onRequireRework.mock.calls[0] as unknown as [
        { reason: string; issues: ReviewIssue[] },
      ]
    )[0];
    expect(input.reason).toContain("stream_sync_aligned@1.1.0");
    expect(input.reason).toContain("wrist");
    expect(input.issues).toHaveLength(1);
    const issue = input.issues[0]!;
    expect(issue.code).toBe("stream_sync_aligned");
    // ERROR 级 QC → BLOCKER 复核问题（严重级别映射固定，不随机）。
    expect(issue.severity).toBe("BLOCKER");
    expect(issue.item_ref).toBe(FAIL_FINDING.finding_id);
    expect(issue.evidence_ref).toContain(RUN.run_id);
  });

  it("未选择依据时不允许提交返工（避免无证据返工）", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({ items: [FAIL_FINDING], next_cursor: null });

    renderSection({ submission: submission({ status: "REJECTED" }) });

    await screen.findByText("stream_sync_aligned");
    expect(
      screen.getByRole("button", { name: /创建返工（附带 QC 证据）/ }),
    ).toBeDisabled();
  });

  it("没有未通过结论时如实说明，并提示无结论不等于通过", async () => {
    listRuns.mockResolvedValue({ items: [RUN], next_cursor: null });
    listFindings.mockResolvedValue({
      items: [PASS_FINDING],
      next_cursor: null,
    });

    renderSection();

    expect(
      await screen.findByText(/没有未通过的质量结论/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/无结论与未运行都不算通过/),
    ).toBeInTheDocument();
  });

  it("Episode 还没有质量运行时如实说明，不当作通过", async () => {
    listRuns.mockResolvedValue({ items: [], next_cursor: null });

    renderSection();

    expect(
      await screen.findByText(/没有未通过的质量结论/),
    ).toBeInTheDocument();
  });

  it("读取失败时展示错误并可重试，不渲染成没有质量问题", async () => {
    listRuns.mockRejectedValue(new Error("boom"));

    renderSection();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    // 失败态下不得出现"没有未通过结论"这种正向结论。
    expect(
      screen.queryByText(/没有未通过的质量结论/),
    ).not.toBeInTheDocument();
  });
});
