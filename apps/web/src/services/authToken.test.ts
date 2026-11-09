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
