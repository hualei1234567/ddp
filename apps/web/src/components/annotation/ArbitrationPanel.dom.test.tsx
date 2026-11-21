import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ARBITER_ID,
  annotationSubmissionFixture,
  REVIEWER_ID,
  USER_ID,
} from "../../services/__fixtures__/annotationFixtures";
import type { ProjectMember } from "../../types/project";
import ArbitrationPanel from "./ArbitrationPanel";

function member(userId: string, displayName: string): ProjectMember {
  return {
    project_id: annotationSubmissionFixture.project_id,
    user_id: userId,
    display_name: displayName,
    role: "editor",
    created_at: null,
    updated_at: null,
  };
}

const ESCALATED = {
  ...annotationSubmissionFixture,
  status: "ESCALATED" as const,
  review_id: REVIEWER_ID,
};

const CANDIDATES = [
  member(USER_ID, "标注员"),
  member(REVIEWER_ID, "复核员"),
  member(ARBITER_ID, "仲裁员"),
];

describe("ArbitrationPanel 仲裁人选择", () => {
  it("从项目成员中选仲裁人，不再要求手抄 UUID", async () => {
    const onCreate = vi.fn();
    render(
      <ArbitrationPanel
        submission={ESCALATED}
        arbitrations={[]}
        candidates={CANDIDATES}
        busyAction={null}
        onCreate={onCreate}
        onAction={vi.fn()}
        onVote={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("combobox", { name: "仲裁人" }));
    // 标注人不出现在候选里：不能给自己的提交仲裁。
    expect(screen.queryByText(/标注员（/)).toBeNull();
    await userEvent.click(await screen.findByText(/仲裁员（/));
    await userEvent.click(screen.getByRole("button", { name: "创建仲裁" }));
    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith({
        arbiterIds: [ARBITER_ID],
        quorum: 1,
        trigger: "REVIEWER_DISAGREEMENT",
        reviewId: REVIEWER_ID,
      }),
    );
  });

  it("未升级的提交不能创建仲裁", () => {
    render(
      <ArbitrationPanel
        submission={annotationSubmissionFixture}
        arbitrations={[]}
        candidates={CANDIDATES}
        busyAction={null}
        onCreate={vi.fn()}
        onAction={vi.fn()}
        onVote={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/只有 ESCALATED 提交可以创建仲裁/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建仲裁" })).toBeDisabled();
  });
});
