import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  annotationSubmissionFixture,
  workbenchDataFixture,
} from "../../services/__fixtures__/annotationFixtures";
import type { WorkbenchBodyProps } from "./WorkbenchBody";
import WorkbenchBody from "./WorkbenchBody";

const baseProps: WorkbenchBodyProps = {
  projectId: workbenchDataFixture.task.project_id,
  data: workbenchDataFixture,
  phase: "ready",
  error: null,
  stale: false,
  partialErrors: [],
  online: true,
  values: {},
  draftReady: true,
  draftNotice: null,
  saveState: "saved",
  storageAvailable: true,
  draftUpdatedAt: 1000,
  validationIssues: [],
  validationAttempted: false,
  dataSource: "REAL",
  disabledReason: null,
  hiddenRequiredLabels: false,
  busyAction: null,
  feedback: null,
  onFieldChange: () => undefined,
  onDataSourceChange: () => undefined,
  onSubmit: () => undefined,
  onSubmitDraft: () => undefined,
  onWithdrawDraft: () => undefined,
  onResetDraft: () => undefined,
  onRetry: () => undefined,
  onBackToList: () => undefined,
};

function render(overrides: Partial<WorkbenchBodyProps> = {}): string {
  return renderToStaticMarkup(<WorkbenchBody {...baseProps} {...overrides} />);
}

describe("标注工作台主体状态", () => {
  it("403 时整页 Forbidden，不展示任务与 Schema 数据", () => {
    const html = render({
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权进入",
        requestId: "req-403",
      }),
    });
    expect(html).toContain("无权访问");
    expect(html).toContain("forbidden");
    expect(html).not.toContain(workbenchDataFixture.task.name);
    expect(html).not.toContain(workbenchDataFixture.schema.code);
  });

  it("Loading 与 Error 状态齐全", () => {
    expect(render({ phase: "loading", data: null })).toContain("正在加载标注工作台");
    const errorHtml = render({
      phase: "error",
      data: null,
      error: new ApiError({
        kind: "http",
        status: 500,
        code: "internal_error",
        message: "内部错误",
      }),
    });
    expect(errorHtml).toContain("加载失败");
    expect(errorHtml).toContain("internal_error");
  });

  it("禁用原因与离线提示明确展示", () => {
    const html = render({
      disabledReason: "当前分配状态为 ASSIGNED，不可填写或提交。",
      online: false,
    });
    expect(html).toContain("当前不可提交");
    expect(html).toContain("ASSIGNED");
    expect(html).toContain("网络离线");
  });

  it("部分数据失败显示 Partial 与错误码", () => {
    const html = render({
      partialErrors: [
        {
          scope: "Episode 详情",
          error: new ApiError({
            kind: "http",
            status: 404,
            code: "resource_not_found",
            message: "不存在",
          }),
        },
      ],
    });
    expect(html).toContain("部分数据加载失败");
    expect(html).toContain("resource_not_found");
  });

  it("Schema 快照不一致时提示旧草稿不加载", () => {
    const html = render({ draftNotice: "incompatible" });
    expect(html).toContain("本地草稿与当前 Schema 快照不一致");
    expect(html).toContain("未加载旧草稿");
  });

  it("存在不可见必填标签时展示 Partial 并禁用提交", () => {
    const html = render({
      hiddenRequiredLabels: true,
      disabledReason: "Schema 含标注员不可见且必填的标签，无法完成提交。",
    });
    expect(html).toContain("当前角色不可见");
    expect(html).toContain("无法完成提交");
  });

  it("服务端已有草稿时只展示草稿与提交/撤回，不展示新表单", () => {
    const html = render({
      data: {
        ...workbenchDataFixture,
        existingDraft: {
          ...annotationSubmissionFixture,
          status: "DRAFT",
          version: 1,
        },
      },
    });
    expect(html).toContain("服务端已有草稿");
    expect(html).toContain("提交该版本");
    expect(html).toContain("撤回草稿");
    expect(html).not.toContain("标注表单");
  });

  it("校验未通过时显示字段级错误与记录级错误", () => {
    const html = render({
      validationAttempted: true,
      validationIssues: [
        {
          field: "55555555-5555-7555-8555-555555555555.action",
          message: "该标签为必填项",
        },
        {
          field: "55555555-5555-7555-8555-555555555555.__record__",
          message: "每条记录至少需要一个标签值",
        },
      ],
    });
    expect(html).toContain("该标签为必填项");
    expect(html).toContain("记录级校验未通过");
    expect(html).toContain("还有 2 项未完成");
  });

  it("没有 Episode 时展示 Empty 并禁用提交", () => {
    const html = render({
      data: { ...workbenchDataFixture, episodes: [] },
      disabledReason: "当前分配没有可标注的 Episode。",
    });
    expect(html).toContain("没有可标注的 Episode");
  });
});
