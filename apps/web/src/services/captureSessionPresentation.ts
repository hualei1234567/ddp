import type { StatusPresentation } from "./datasetPresentation";
import { CAPTURE_SESSION_ACTION_VALUES, type CaptureSessionAction, type CaptureSessionArtifactKind } from "../types/captureSession";

const STATUS: Record<string, StatusPresentation> = {
  SCHEDULED: { label: "待开始", color: "default" }, RUNNING: { label: "采集中", color: "processing" },
  PAUSED: { label: "已暂停", color: "warning" }, COMPLETED: { label: "已完成", color: "success" },
  ABORTED: { label: "已中止", color: "error" },
};

const ACTIONS: Record<CaptureSessionAction, { label: string; requiresReason: boolean; danger: boolean }> = {
  start: { label: "开始采集", requiresReason: false, danger: false },
  pause: { label: "暂停采集", requiresReason: true, danger: false },
  resume: { label: "恢复采集", requiresReason: true, danger: false },
  complete: { label: "完成采集", requiresReason: false, danger: false },
  abort: { label: "中止会话", requiresReason: true, danger: true },
};

export function presentCaptureSessionStatus(value: string): StatusPresentation {
  return STATUS[value] ?? { label: value, color: "default" };
}

export function resolveCaptureSessionActions(values: string[]) {
  return values.flatMap((value) => {
    const action = CAPTURE_SESSION_ACTION_VALUES.find((item) => item === value);
    return action ? [{ action, ...ACTIONS[action] }] : [];
  });
}

export function captureSessionArtifactPath(kind: CaptureSessionArtifactKind, id: string): string {
  const encoded = encodeURIComponent(id);
  return kind === "RAW_ASSET" ? `/raw-assets/${encoded}` : `/episodes/${encoded}`;
}
