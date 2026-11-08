import { describe, expect, it } from "vitest";

import { formatPhase } from "./format";

describe("formatPhase", () => {
  it("统一阶段文案", () => {
    expect(formatPhase("0")).toBe("阶段 0");
  });
});
