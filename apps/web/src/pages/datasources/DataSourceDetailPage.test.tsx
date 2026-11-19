import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type {
  DataSource,
  DataSourceDetail,
  DataSourceImport,
} from "../../types/datasource";
import {
  DataSourceDetailBody,
  type DataSourceDetailBodyProps,
} from "./DataSourceDetailPage";

const PROJECT = "11111111-1111-1111-1111-111111111111";
const SOURCE = "22222222-2222-2222-2222-222222222222";
const IMPORT = "33333333-3333-3333-3333-333333333333";
const SHA = "0123456789abcdef0123456789abcdef01234567";

const source: DataSource = {
  data_source_id: SOURCE,
  project_id: PROJECT,
  code: "hf-demo",
  name: "示例数据集",
  kind: "HUGGINGFACE",
  repo_id: "lerobot/aloha_sim_insertion_human",
  revision: SHA,
  license: "Apache-2.0",
  access_condition: "PUBLIC",
  is_gated: false,
  config_name: null,
  split: null,
  dataset_card_url: "https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
  notes: null,
  metadata: {},
  version: 1,
};

const dataImport: DataSourceImport = {
  import_id: IMPORT,
  project_id: PROJECT,
  data_source_id: SOURCE,
  status: "SUCCEEDED",
  scope: "SAMPLE",
  tool_name: "huggingface_hub",
  tool_version: "0.27.1",
  sample_limit: 10,
  cache_location: null,
  imported_row_count: 10,
  imported_file_count: 2,
  error_message: null,
  started_at: "2026-11-18T00:00:00Z",
  finished_at: "2026-11-18T00:01:00Z",
  stage: "COMPLETE",
  processed_items: 2,
  total_items: 2,
  version: 2,
  allowed_actions: [],
};

const detail: DataSourceDetail = {
  data_source: source,
  imports: [dataImport],
  remote_check: {
    status: "VERIFIED",
    checked_at: "2026-11-18T00:00:00Z",
    resolved_revision: SHA,
    license: "Apache-2.0",
    is_gated: false,
    dataset_card_url:
      "https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
  },
};

const baseProps: DataSourceDetailBodyProps = {
  detail,
  loading: false,
  error: null,
  stale: false,
  onRetry: () => undefined,
};

function render(overrides: Partial<DataSourceDetailBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <DataSourceDetailBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("数据源详情页状态", () => {
  it("加载中显示 Loading，不冒充空数据", () => {
    const html = render({ detail: null, loading: true });

    expect(html).toContain("正在加载数据源详情");
    expect(html).not.toContain("暂无导入任务");
  });

  it("404 显示不存在空态并给出返回入口", () => {
    const html = render({
      detail: null,
      loading: false,
      error: new ApiError({
        kind: "http",
        status: 404,
        code: "resource_not_found",
        message: "资源不存在",
      }),
    });

    expect(html).toContain("数据源不存在或无权查看");
    expect(html.replace(/\s+/g, "")).toContain("返回数据源列表");
  });

  it("加载失败显示 Error 与重试，不展示详情", () => {
    const html = render({
      detail: null,
      loading: false,
      error: new ApiError({
        kind: "http",
        status: 500,
        code: "internal_error",
        message: "服务端错误",
      }),
    });

    expect(html).toContain("加载失败");
    expect(html).toContain("服务端错误");
    expect(html.replace(/\s+/g, "")).toContain("重试");
    expect(html).not.toContain(source.repo_id);
  });

  it("403 整页 Forbidden，不渲染业务字段", () => {
    const html = render({
      detail: null,
      loading: false,
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权访问该项目",
      }),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(source.name);
  });

  it("就绪时展示登记信息、校验结论与导入任务", () => {
    const html = render();

    expect(html).toContain(source.name);
    expect(html).toContain(source.repo_id);
    expect(html).toContain(source.revision);
    expect(html).toContain(source.license);
    expect(html).toContain("已验证");
    expect(html).toContain(dataImport.import_id.slice(0, 8));
    expect(html).toContain("SUCCEEDED");
  });

  it("未校验时明确显示未校验，不渲染成通过", () => {
    const html = render({
      detail: { ...detail, remote_check: null },
    });

    expect(html).toContain("尚未进行远端校验");
    expect(html).not.toContain("已验证");
  });

  it("已校验但远端未声明许可证时显示 Partial，不显示干净的已验证", () => {
    const html = render({
      detail: {
        ...detail,
        remote_check: { ...detail.remote_check!, license: null },
      },
    });

    expect(html).toContain("部分信息（远端未声明许可证）");
    expect(html).not.toContain(">已验证<");
    expect(html).toContain("未声明（不代表无限制）");
  });

  it("没有导入任务时显示空态", () => {
    const html = render({ detail: { ...detail, imports: [] } });

    expect(html).toContain("暂无导入任务");
  });

  it("刷新失败保留详情并提示 Stale", () => {
    const html = render({
      stale: true,
      error: new ApiError({
        kind: "network",
        message: "网络不可用",
      }),
    });

    expect(html).toContain("最近一次刷新失败");
    expect(html).toContain(source.repo_id);
  });
});
