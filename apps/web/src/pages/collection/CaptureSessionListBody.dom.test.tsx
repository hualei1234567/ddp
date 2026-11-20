import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../services/api";
import CaptureSessionListBody from "./CaptureSessionListBody";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});

const base = { items: [], loading: false, refreshing: false, loadingMore: false, hasMore: false, error: null, loadMoreError: null, stale: false, onRetry: vi.fn(), onLoadMore: vi.fn() };

describe("采集会话列表状态", () => {
  it("覆盖 Loading、Empty、Forbidden、Stale", () => {
    expect(renderToStaticMarkup(<CaptureSessionListBody {...base} loading />)).toContain("正在加载采集会话");
    expect(renderToStaticMarkup(<CaptureSessionListBody {...base} />)).toContain("暂无采集会话");
    const forbidden = new ApiError({ kind: "http", status: 403, message: "禁止访问" });
    expect(renderToStaticMarkup(<CaptureSessionListBody {...base} error={forbidden} />)).toContain("禁止访问");
    render(<MemoryRouter><CaptureSessionListBody {...base} stale items={[{ session_id: "s", project_id: "p", code: "C", task_id: null, batch_id: null, operator_id: null, robot_id: null, location_id: null, status: "SCHEDULED", started_at: null, ended_at: null, status_reason: null, allowed_actions: [], version: 1, created_at: null, updated_at: null, created_by: null, updated_by: null }]} /></MemoryRouter>);
    expect(screen.getByText(/上次成功获取/)).toBeInTheDocument();
  });
});
