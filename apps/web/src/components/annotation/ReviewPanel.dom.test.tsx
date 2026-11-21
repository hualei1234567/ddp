import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  annotationSubmissionFixture,
  REVIEWER_ID,
  USER_ID,
} from "../../services/__fixtures__/annotationFixtures";
import type { ProjectMember } from "../../types/project";
import ReviewPanel from "./ReviewPanel";

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

const CANDIDATES = [member(USER_ID, "标注员"), member(REVIEWER_ID, "复核员")];

describe("ReviewPanel 复核人选择", () => {
  it("默认以当前认证用户作为复核人，创建时不再需要手抄 UUID", async () => {
    const onCreate = vi.fn();
    render(
      <ReviewPanel
        submission={annotationSubmissionFixture}
        reviews={[]}
        candidates={CANDIDATES}
        currentUserId={REVIEWER_ID}
        busyAction={null}
        onCreate={onCreate}
        onAction={vi.fn()}
      />,
    );
    const input = screen.getByRole("combobox", { name: "复核人" });
    expect(input).toHaveValue(`${REVIEWER_ID}`);
    await userEvent.click(screen.getByRole("button", { name: "创建复核" }));
    expect(onCreate).toHaveBeenCalledWith(REVIEWER_ID);
  });

  it("当前用户就是标注人时不预填，并说明服务端会拒绝自审", () => {
    render(
      <ReviewPanel
        submission={annotationSubmissionFixture}
        reviews={[]}
        candidates={CANDIDATES}
        currentUserId={USER_ID}
        busyAction={null}
        onCreate={vi.fn()}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByRole("combobox", { name: "复核人" })).toHaveValue("");
    expect(screen.getByText(/当前用户就是本提交的标注人/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建复核" })).toBeDisabled();
  });

  it("候选列表只给非标注人成员建议，且降级时提示只能手填", async () => {
    const onCreate = vi.fn();
    render(
      <ReviewPanel
        submission={annotationSubmissionFixture}
        reviews={[]}
        candidates={CANDIDATES}
        currentUserId={null}
        candidatesDegraded
        busyAction={null}
        onCreate={onCreate}
        onAction={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/身份或成员列表读取失败/),
    ).toBeInTheDocument();
    const input = screen.getByRole("combobox", { name: "复核人" });
    await userEvent.type(input, REVIEWER_ID);
    await waitFor(() => expect(input).toHaveValue(REVIEWER_ID));
    await userEvent.click(screen.getByRole("button", { name: "创建复核" }));
    expect(onCreate).toHaveBeenCalledWith(REVIEWER_ID);
    expect(screen.queryByText(/标注员/)).toBeNull();
  });
});
