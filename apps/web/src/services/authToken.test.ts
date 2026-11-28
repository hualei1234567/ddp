import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAccessToken,
  readAccessToken,
  storeAccessToken,
} from "./authToken";

function createStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

describe("访问令牌存储", () => {
  beforeEach(() => {
    vi.stubGlobal("sessionStorage", createStorage());
  });

  afterEach(() => {
    clearAccessToken();
    vi.unstubAllGlobals();
  });

  it("保存并读取去空白后的令牌", () => {
    const stored = storeAccessToken("  token-value  ");
    expect(stored).toBe("token-value");
    expect(readAccessToken()).toBe("token-value");
  });

  it("自动剥离粘贴时附带的 Bearer 前缀", () => {
    const stored = storeAccessToken("  Bearer   eyJ.test.sig  ");
    expect(stored).toBe("eyJ.test.sig");
    expect(readAccessToken()).toBe("eyJ.test.sig");
  });

  it("仅输入 Bearer 前缀而无实际令牌时不保存", () => {
    expect(storeAccessToken("Bearer   ")).toBeNull();
    expect(readAccessToken()).toBeNull();
  });

  it("空令牌不保存", () => {
    expect(storeAccessToken("   ")).toBeNull();
    expect(readAccessToken()).toBeNull();
  });

  it("清除后读取为 null", () => {
    storeAccessToken("token-value");
    clearAccessToken();
    expect(readAccessToken()).toBeNull();
  });
});
