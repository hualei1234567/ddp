import { describe, expect, it, vi } from "vitest";

import {
  PROJECT_ID,
  SESSION_ID,
  uploadPartFixture,
} from "./__fixtures__/collectionFixtures";
import { ApiClient, ApiError } from "./api";
import { createUploadsApi } from "./uploads";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

const checksum =
  "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

const partUrlResponse = {
  part_number: 1,
  method: "PUT",
  url: "https://storage.example/part-1?sig=bound",
  headers: {
    "Content-Length": "16",
    "x-amz-checksum-sha256": "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
  },
  expires_at: "2026-11-08T12:15:00Z",
};

describe("分片预签名 URL 请求", () => {
  it("先带 sha256 请求 URL，并原样返回服务端 headers", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(partUrlResponse),
    );
    const api = createUploadsApi(clientWith(fetchImpl));

    const response = await api.createPartUrl(
      PROJECT_ID,
      SESSION_ID,
      1,
      { sha256: checksum },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/upload-sessions/${SESSION_ID}/parts/1/url`,
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ sha256: checksum });
    expect(response.headers).toEqual(partUrlResponse.headers);
  });

  it("旧服务端兼容：未提供 sha256 时不发送请求体", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        ...partUrlResponse,
        headers: { "Content-Length": "16" },
      }),
    );
    const api = createUploadsApi(clientWith(fetchImpl));

    const response = await api.createPartUrl(PROJECT_ID, SESSION_ID, 1);
    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.body).toBeUndefined();
    expect(response.headers).toEqual({ "Content-Length": "16" });
  });

  it("403/409 保持统一 ApiError，不把冲突当成功", async () => {
    const forbidden = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "forbidden",
            message: "无权上传",
            request_id: "rid-403",
          },
        },
        403,
      ),
    );
    const api = createUploadsApi(clientWith(forbidden));
    const failure = api.createPartUrl(PROJECT_ID, SESSION_ID, 1, {
      sha256: checksum,
    });
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
      requestId: "rid-403",
    });

    const conflict = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "upload_part_conflict",
            message: "分片冲突",
            request_id: "rid-409",
          },
        },
        409,
      ),
    );
    const conflictApi = createUploadsApi(clientWith(conflict));
    await expect(
      conflictApi.createPartUrl(PROJECT_ID, SESSION_ID, 1, {
        sha256: uploadPartFixture.sha256,
      }),
    ).rejects.toMatchObject({ status: 409, code: "upload_part_conflict" });
  });
});
