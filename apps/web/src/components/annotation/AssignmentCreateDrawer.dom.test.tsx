import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AssignmentCapacityPreview,
  AssignmentCapacityPreviewRequest,
} from "../../types/annotationCapacityPreview";
import { AssignmentCapacitySummary } from "./AssignmentCapacityPreviewPanel";
import AssignmentCreateDrawer from "./AssignmentCreateDrawer";

/**
 * 分配前实时负载提示（FR-020）。
 *
 * 语义红线（任何一条破了都应让这里红）：
 *
 * 1. **选中即查**：选人被分配人就发预览请求，不需要点"预览"或提交；
 * 2. **提示不阻断**：`will_exceed=true` 时"创建"按钮仍然可点——是否放行由
 *    服务端 `CapacityPolicy` 硬阻断裁决，前端不复制阻断逻辑；
 * 3. **缺失不当 0**：`max_*` 为 null 时显示「未配置」，不画成 0；
 * 4. **预览失败不阻断提交**：预览失败只降级提示。
 *
 * 这里 mock 的是 `annotationApi`（网络边界），不是被测绘的抽屉组件本身。
 */

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const BATCH_ID = "22222222-2222-7222-8222-222222222222";
const MEMBER_LOW = "33333333-3333-7333-8333-333333333333";
const MEMBER_HIGH = "44444444-4444-7444-8444-444444444444";

const previewAssignmentCapacity = vi.fn();
const listMembers = vi.fn();

vi.mock("../../services/annotation", () => ({
  annotationApi: {
    previewAssignmentCapacity: (
      ...args: [string, AssignmentCapacityPreviewRequest, unknown]
    ) => previewAssignmentCapacity(...args),
  },
}));

vi.mock("../../services/projectManagement", () => ({
  projectManagementApi: {
    listMembers: (...args: unknown[]) => listMembers(...args),
  },
}));

function member(userId: string, displayName: string) {
  return {
    project_id: PROJECT_ID,
    user_id: userId,
    display_name: displayName,
    role: "editor" as const,
    created_at: null,
    updated_at: null,
  };
}

const MEMBERS = [member(MEMBER_LOW, "轻载标注员"), member(MEMBER_HIGH, "满负荷标注员")];

function previewFixture(
  overrides: Partial<AssignmentCapacityPreview> = {},
): AssignmentCapacityPreview {
  return {
    assignee_id: MEMBER_LOW,
    open_item_count: 2,
    open_planned_minutes: 60,
    planned_item_count: 3,
    planned_minutes: 90,
    projected_item_count: 5,
    projected_planned_minutes: 150,
    max_open_items: 10,
    max_planned_minutes: 300,
    item_capacity_ratio: 0.5,
    minutes_capacity_ratio: 0.5,
    will_exceed: false,
    exceeds_open_items: false,
    exceeds_planned_minutes: false,
    status: "NORMAL",
    ...overrides,
  };
}

/** 超容量形态：项数与工时都突破上限。 */
function overCapacityFixture(): AssignmentCapacityPreview {
  return previewFixture({
    open_item_count: 9,
    open_planned_minutes: 290,
    projected_item_count: 12,
    projected_planned_minutes: 380,
    item_capacity_ratio: 1.2,
    minutes_capacity_ratio: 1.267,
    will_exceed: true,
    exceeds_open_items: true,
    exceeds_planned_minutes: true,
    status: "OVER_CAPACITY",
  });
}

function renderDrawer() {
  return render(
    <AssignmentCreateDrawer
      open
      projectId={PROJECT_ID}
      batches={[
        {
          batch_id: BATCH_ID,
          project_id: PROJECT_ID,
          task_id: "55555555-5555-7555-8555-555555555555",
          sequence_no: 1,
          code: "B-1",
          status: "READY",
          planned_item_count: 10,
          item_refs: [],
          version: 1,
          created_at: null,
          updated_at: null,
        } as never,
      ]}
      onClose={vi.fn()}
      onSubmit={vi.fn().mockResolvedValue(undefined)}
    />,
  );
}

/**
 * antd `Select` 首次展开在 jsdom 下需要 3~8 秒（虚拟列表 + 定位重排），
 * 单个用例默认 5 秒超时会在全量并行跑时必然超时。这里显式放宽超时：
 * 慢的是 jsdom 渲染 antd 下拉，不是被测逻辑，也不该靠改组件语义（例如换成
 * AutoComplete）来"提速"——那会让测试测的不再是真实交互。
 */
const SLOW_DOM_MS = 40_000;

/**
 * antd 会在两字中文按钮文案之间插空格（"创建" → "创 建"），
 * 因此按钮断言统一用正则匹配，避免只因为渲染细节改文案就红。
 */
function createButton(): HTMLElement {
  return screen.getByRole("button", { name: /创\s*建/ });
}

/** 选择人员并等待预览请求真正发出（防抖 250ms）。 */
async function selectAssignee(displayName: string) {
  // delay: null 关掉 userEvent 的逐键延迟；这里没有输入型交互，
  // 保留默认延迟只会把 8 个用例拖成两分钟。
  //
  // 已知成本：antd Select 首次展开在 jsdom 下约 3~4 秒（虚拟列表 + 定位
  // 重排），本文件 6 个「选中人员」用例因此各需数秒。不为提速改组件语义
  // （例如换成 AutoComplete）——那会改变真实交互，属于为测试改产品。
  const user = userEvent.setup({ delay: null });
  await user.click(await screen.findByRole("combobox", { name: "人员 ID" }));
  await user.click(await screen.findByText(new RegExp(displayName)));
  return user;
}

beforeEach(() => {
  previewAssignmentCapacity.mockReset();
  listMembers.mockReset();
  listMembers.mockResolvedValue({ items: MEMBERS, next_cursor: null });
  previewAssignmentCapacity.mockResolvedValue([previewFixture()]);
});

describe("AssignmentCreateDrawer 分配前负载提示", () => {
  it("选中被分配人后立即请求负载，不需要提交", async () => {
    renderDrawer();

    await selectAssignee("轻载标注员");

    await waitFor(() => expect(previewAssignmentCapacity).toHaveBeenCalledTimes(1));
    expect(previewAssignmentCapacity.mock.calls[0]?.[0]).toBe(PROJECT_ID);
    expect(previewAssignmentCapacity.mock.calls[0]?.[1]).toMatchObject({
      assignee_ids: [MEMBER_LOW],
    });
  }, SLOW_DOM_MS);

  it("展示该人当前负载与分配后投影", async () => {
    previewAssignmentCapacity.mockResolvedValue([
      previewFixture({ assignee_id: MEMBER_LOW }),
    ]);
    renderDrawer();

    await selectAssignee("轻载标注员");

    // 下拉与负载条都含显示名，因此按"出现即可"断言而不是唯一匹配。
    expect((await screen.findAllByText(/轻载标注员/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/当前进行中：2 项/)).toBeInTheDocument();
    expect(await screen.findByText(/分配后 5 项/)).toBeInTheDocument();
  }, SLOW_DOM_MS);

  it("超容量时给出明确警示，但创建按钮仍可用（不重复实现阻断）", async () => {
    previewAssignmentCapacity.mockResolvedValue([
      overCapacityFixture(),
    ]);
    renderDrawer();

    await selectAssignee("轻载标注员");

    expect(
      await screen.findByText(/本次分配后将超出服务端配置的容量上限/),
    ).toBeInTheDocument();
    expect((await screen.findAllByText(/将超上限/)).length).toBeGreaterThan(0);
    // 红线：提示不阻断。服务端才是唯一阻断点。
    expect(createButton()).toBeEnabled();
  }, SLOW_DOM_MS);

  it("上限未配置时显示「未配置」而不是 0", async () => {
    previewAssignmentCapacity.mockResolvedValue([
      previewFixture({
        max_open_items: null,
        max_planned_minutes: null,
        item_capacity_ratio: null,
        minutes_capacity_ratio: null,
      }),
    ]);
    renderDrawer();

    await selectAssignee("轻载标注员");

    expect(await screen.findByText(/项数未配置/)).toBeInTheDocument();
    expect(await screen.findByText(/工时未配置/)).toBeInTheDocument();
    expect(screen.queryByText(/上限：0 项/)).toBeNull();
  }, SLOW_DOM_MS);

  it("预览失败时降级提示，不阻断创建", async () => {
    previewAssignmentCapacity.mockRejectedValue(new Error("boom"));
    renderDrawer();

    await selectAssignee("轻载标注员");

    expect(await screen.findByText(/负载提示暂不可用/)).toBeInTheDocument();
    expect(createButton()).toBeEnabled();
  }, SLOW_DOM_MS);

  it("未选择人员时不发预览请求", async () => {
    renderDrawer();

    await screen.findByRole("combobox", { name: "人员 ID" });

    expect(previewAssignmentCapacity).not.toHaveBeenCalled();
  }, SLOW_DOM_MS);
});

describe("AssignmentCapacitySummary 展示语义", () => {
  it("正好达到上限显示橙色「正好达到上限」，不显示超限文案", () => {
    render(
      <AssignmentCapacitySummary
        preview={previewFixture({
          projected_item_count: 10,
          item_capacity_ratio: 1,
          status: "AT_CAPACITY",
        })}
        assigneeLabel="张三"
      />,
    );

    expect(screen.getByText(/正好达到上限/)).toBeInTheDocument();
    expect(screen.queryByText(/本次分配后将超出/)).toBeNull();
  });

  it("超容量时逐条说明是项数还是工时超限", () => {
    render(
      <AssignmentCapacitySummary
        preview={previewFixture({
          will_exceed: true,
          exceeds_open_items: true,
          exceeds_planned_minutes: false,
          status: "OVER_CAPACITY",
          projected_item_count: 12,
        })}
        assigneeLabel="李四"
      />,
    );

    expect(screen.getByText(/项数将超上限/)).toBeInTheDocument();
    expect(screen.queryByText(/工时将超上限/)).toBeNull();
  });
});
