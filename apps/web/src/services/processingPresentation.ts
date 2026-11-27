/** 处理运行枚举的中文展示；只做展示，不推断权限或状态迁移。 */

import type { StatusPresentation } from "./datasetPresentation";

const lookup = (
  table: Record<string, StatusPresentation>,
  value: string,
): StatusPresentation => table[value] ?? { label: value, color: "default" };

const RUN_STATUS: Record<string, StatusPresentation> = {
  CREATED: { label: "已创建", color: "default" },
  QUEUED: { label: "排队中", color: "default" },
  RUNNING: { label: "处理中", color: "processing" },
  SUCCEEDED: { label: "已完成", color: "green" },
  FAILED: { label: "失败", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
  RETRYING: { label: "重试中", color: "orange" },
};

const STEP_STATUS: Record<string, StatusPresentation> = {
  RUNNING: { label: "执行中", color: "processing" },
  SUCCEEDED: { label: "成功", color: "green" },
  FAILED: { label: "失败", color: "red" },
  // SKIPPED 是一等状态：该阶段在当前流水线版本下没有真实实现，
  // 不渲染成「成功」。
  SKIPPED: { label: "已跳过", color: "default" },
};

const STAGE: Record<string, string> = {
  FILE_VALIDATION: "文件校验",
  METADATA_EXTRACTION: "元数据提取",
  FORMAT_PARSING: "格式解析",
  TIME_ALIGNMENT: "时间对齐",
  CALIBRATION_VALIDATION: "标定校验",
  STREAM_EXTRACTION: "流提取",
  EPISODE_SEGMENTATION: "Episode 切分",
  DATA_CLEANING: "数据清洗",
  QUALITY_PRE_CHECK: "质量预检",
};

export const presentRunStatus = (value: string) => lookup(RUN_STATUS, value);
export const presentStepStatus = (value: string) => lookup(STEP_STATUS, value);

export function presentStage(value: string | null): string {
  if (!value) {
    return "—";
  }
  return STAGE[value] ?? value;
}

/** started→finished 的展示耗时；任一端缺失或区间非法时如实展示「—」。 */
export function formatRunDuration(
  startedAt: string | null,
  finishedAt: string | null,
): string {
  if (!startedAt || !finishedAt) {
    return "—";
  }
  const start = new Date(startedAt).getTime();
  const end = new Date(finishedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) {
    return "—";
  }
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) {
    return `${minutes}m${rest}s`;
  }
  return `${Math.floor(minutes / 60)}h${minutes % 60}m${rest}s`;
}
