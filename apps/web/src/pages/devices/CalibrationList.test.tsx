import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { backendCalibrationFixture } from "../../services/__fixtures__/deviceContract.fixtures";
import type { CalibrationRecord } from "../../types/device";
import { CalibrationFreshnessNotice } from "./CalibrationList";

const NOW = Date.parse("2026-11-08T12:00:00Z");

function renderNotice(records: CalibrationRecord[], stale = false): string {
  return renderToStaticMarkup(
    <CalibrationFreshnessNotice
      records={records}
      stale={stale}
      onRetry={() => undefined}
      now={NOW}
    />,
  );
}

describe("标定过期 Stale 提示", () => {
  it("valid_to 已过期时提示重新标定", () => {
    const html = renderNotice([
      {
        ...backendCalibrationFixture,
        valid_to: "2026-11-01T00:00:00Z",
        status: "ACTIVE",
      },
    ]);

    expect(html).toContain("存在已过期标定");
    expect(html).toContain("已过期 1 条");
    expect(html).toContain("请重新标定");
  });

  it("有效期为空且未刷新失败时不显示 Stale", () => {
    expect(
      renderNotice([
        { ...backendCalibrationFixture, valid_to: null, status: "ACTIVE" },
      ]),
    ).toBe("");
  });

  it("刷新失败时合并展示旧数据提示", () => {
    const html = renderNotice(
      [
        { ...backendCalibrationFixture, valid_to: null, status: "ACTIVE" },
      ],
      true,
    );

    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的数据");
  });
});
