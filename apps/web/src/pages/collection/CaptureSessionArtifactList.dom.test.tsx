import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../services/api";
import CaptureSessionArtifactList from "./CaptureSessionArtifactList";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});

const base = { items: [], loading: false, refreshing: false, loadingMore: false, hasMore: false, error: null, loadMoreError: null, stale: false, onRetry: vi.fn(), onLoadMore: vi.fn() };

describe("会话产物独立状态", () => {
  it("空列表为合法终态，错误不影响详情文案", () => {
    expect(renderToStaticMarkup(<CaptureSessionArtifactList {...base} />)).toContain("尚无产物");
    const error = new ApiError({ kind: "network", message: "产物接口失败" });
    expect(renderToStaticMarkup(<CaptureSessionArtifactList {...base} error={error} />)).toContain("产物接口失败");
  });

  it("产物链接指向已核对的真实详情路由", () => {
    render(<MemoryRouter><CaptureSessionArtifactList {...base} items={[{ kind: "RAW_ASSET", artifact_id: "asset-1", code: "RAW-1", status: "READY", created_at: "2026-11-20T00:00:00Z" }, { kind: "EPISODE", artifact_id: "episode-1", code: "EP-1", status: "READY", created_at: "2026-11-20T00:00:00Z" }]} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "RAW-1" })).toHaveAttribute("href", "/raw-assets/asset-1");
    expect(screen.getByRole("link", { name: "EP-1" })).toHaveAttribute("href", "/episodes/episode-1");
  });
});
