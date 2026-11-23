/**
 * 批量重跑 / 失败项重试的展示辅助（V31-033 / FR-026 + FR-028）。
 *
 * FR-028 的筛选语义在纯函数里固定：**只有**上一次运行结论为 FAILED
 * 的对象进入重试集合。`INCOMPLETE`（无结论）与 `NOT_CHECKED`（未质检）
 * 都不是失败，不得混入；页面不得绕开本函数自行拼装列表。
 */
import type { QualityRunStatus } from "../types/qualityRun";

/** 重跑候选：`lastStatus` 为 null 表示该对象还没有任何质量运行。 */
export interface QualityRerunSubject {
  subjectId: string;
  lastStatus: QualityRunStatus | null;
}

/** 上一次运行结论为 FAILED 的对象；无历史运行的（null）不重试。 */
export function filterFailedSubjects(
  subjects: QualityRerunSubject[],
): QualityRerunSubject[] {
  return subjects.filter((item) => item.lastStatus === "FAILED");
}
