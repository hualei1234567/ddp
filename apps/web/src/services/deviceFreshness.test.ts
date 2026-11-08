import { describe, expect, it } from "vitest";

import { backendCalibrationFixture, backendDeviceFixture } from "./__fixtures__/deviceContract.fixtures";
import {
  evaluateDeviceFreshness,
  isCalibrationExpired,
  summarizeCalibrationExpiry,
  summarizeDeviceFreshness,
} from "./deviceFreshness";
import type { Device } from "../types/device";

const NOW = Date.parse("2026-11-08T12:00:00Z");

const device: Device = { ...backendDeviceFixture };

describe("设备心跳新鲜度", () => {
  it("心跳在服务端阈值内判定为 fresh", () => {
    const freshness = evaluateDeviceFreshness(
      {
        ...device,
        last_heartbeat_at: "2026-11-08T11:59:30Z",
        heartbeat_timeout_seconds: 60,
      },
      NOW,
    );

    expect(freshness.level).toBe("fresh");
    expect(freshness.reason).toBe("heartbeat");
  });

  it("心跳超过服务端阈值判定为 stale", () => {
    const freshness = evaluateDeviceFreshness(
      {
        ...device,
        last_heartbeat_at: "2026-11-08T11:50:00Z",
        heartbeat_timeout_seconds: 60,
      },
      NOW,
    );

    expect(freshness).toMatchObject({
      level: "stale",
      reason: "heartbeat",
      lastHeartbeatAt: "2026-11-08T11:50:00Z",
      timeoutSeconds: 60,
    });
  });

  it("服务端未下发阈值时保持 unknown，不由前端猜测", () => {
    const freshness = evaluateDeviceFreshness(
      { ...device, last_heartbeat_at: "2026-01-01T00:00:00Z" },
      NOW,
    );

    expect(freshness.level).toBe("unknown");
  });

  it("服务端明确 freshness=stale 时优先于心跳时间", () => {
    const freshness = evaluateDeviceFreshness(
      {
        ...device,
        freshness: "stale",
        last_heartbeat_at: "2026-11-08T11:59:59Z",
        heartbeat_timeout_seconds: 60,
      },
      NOW,
    );

    expect(freshness).toMatchObject({ level: "stale", reason: "server" });
  });

  it("非法心跳时间不误判为过期", () => {
    const freshness = evaluateDeviceFreshness(
      {
        ...device,
        last_heartbeat_at: "not-a-date",
        heartbeat_timeout_seconds: 60,
      },
      NOW,
    );

    expect(freshness.level).toBe("unknown");
  });

  it("汇总超时设备并给出最近心跳", () => {
    const summary = summarizeDeviceFreshness(
      [
        {
          ...device,
          device_id: "stale-1",
          last_heartbeat_at: "2026-11-08T11:00:00Z",
          heartbeat_timeout_seconds: 60,
        },
        {
          ...device,
          device_id: "stale-2",
          last_heartbeat_at: "2026-11-08T11:30:00Z",
          heartbeat_timeout_seconds: 60,
        },
        {
          ...device,
          device_id: "fresh-1",
          last_heartbeat_at: "2026-11-08T11:59:30Z",
          heartbeat_timeout_seconds: 60,
        },
        { ...device, device_id: "unknown-1" },
      ],
      NOW,
    );

    expect(summary.staleCount).toBe(2);
    expect(summary.unknownCount).toBe(1);
    expect(summary.staleDeviceIds).toEqual(["stale-1", "stale-2"]);
    expect(summary.latestHeartbeatAt).toBe("2026-11-08T11:30:00Z");
  });
});

describe("标定有效期新鲜度", () => {
  it("valid_to 已过判定为过期", () => {
    expect(
      isCalibrationExpired(
        { ...backendCalibrationFixture, valid_to: "2026-11-08T11:59:59Z" },
        NOW,
      ),
    ).toBe(true);
  });

  it("valid_to 为空表示长期有效", () => {
    expect(
      isCalibrationExpired(
        { ...backendCalibrationFixture, valid_to: null, status: "ACTIVE" },
        NOW,
      ),
    ).toBe(false);
  });

  it("服务端已标记 EXPIRED 时即使 valid_to 为空也视为过期", () => {
    expect(
      isCalibrationExpired(
        { ...backendCalibrationFixture, valid_to: null, status: "EXPIRED" },
        NOW,
      ),
    ).toBe(true);
  });

  it("汇总过期条数与最近有效期截止时间", () => {
    const summary = summarizeCalibrationExpiry(
      [
        {
          ...backendCalibrationFixture,
          status: "ACTIVE",
          valid_to: "2026-11-01T00:00:00Z",
        },
        {
          ...backendCalibrationFixture,
          status: "ACTIVE",
          valid_to: "2026-11-07T00:00:00Z",
        },
        { ...backendCalibrationFixture, valid_to: null, status: "ACTIVE" },
      ],
      NOW,
    );

    expect(summary.expiredCount).toBe(2);
    expect(summary.latestExpiredValidTo).toBe("2026-11-07T00:00:00Z");
  });

  it("历史记录已过有效期时不触发当前标定告警", () => {
    const summary = summarizeCalibrationExpiry(
      [
        {
          ...backendCalibrationFixture,
          status: "SUPERSEDED",
          valid_to: "2026-10-01T00:00:00Z",
        },
        {
          ...backendCalibrationFixture,
          status: "REVOKED",
          valid_to: "2026-10-01T00:00:00Z",
        },
        {
          ...backendCalibrationFixture,
          status: "ACTIVE",
          valid_to: "2026-12-01T00:00:00Z",
        },
      ],
      NOW,
    );

    expect(summary.expiredCount).toBe(0);
    expect(summary.latestExpiredValidTo).toBeNull();
  });
});
