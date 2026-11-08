import { describe, expect, it } from "vitest";

import {
  ContractValidationError,
  parseErrorEnvelope,
  parseHealthResponse,
  parsePlatformMeta,
} from "./validation";

describe("契约校验", () => {
  it("接受合法的健康检查响应", () => {
    expect(
      parseHealthResponse({
        status: "ok",
        service: "DDP",
        version: "0.1.0",
        env: "local",
      }),
    ).toEqual({
      status: "ok",
      service: "DDP",
      version: "0.1.0",
      env: "local",
    });
  });

  it("拒绝缺少字段的健康检查响应", () => {
    expect(() =>
      parseHealthResponse({ status: "ok", service: "DDP" }),
    ).toThrow(ContractValidationError);
  });

  it("接受合法的平台元信息并复制 modules 数组", () => {
    const input = {
      name: "DDP",
      version: "0.1.0",
      env: "local",
      phase: "0",
      modules: ["platform"],
    };
    expect(parsePlatformMeta(input)).toEqual(input);
  });

  it("拒绝非字符串 modules 元素", () => {
    expect(() =>
      parsePlatformMeta({
        name: "DDP",
        version: "0.1.0",
        env: "local",
        phase: "0",
        modules: ["platform", 1],
      }),
    ).toThrow(ContractValidationError);
  });

  it("解析合法错误体", () => {
    expect(
      parseErrorEnvelope({
        error: { code: "not_found", message: "资源不存在", request_id: null },
      }),
    ).toEqual({
      error: { code: "not_found", message: "资源不存在", request_id: null },
    });
  });

  it("非契约错误体返回 null", () => {
    expect(parseErrorEnvelope({ message: "oops" })).toBeNull();
    expect(parseErrorEnvelope("oops")).toBeNull();
  });
});
