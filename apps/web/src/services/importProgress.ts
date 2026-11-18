/** 导入阶段/进度的展示口径：服务端可空，前端绝不把 null 补成 0。 */

import type { ImportStage } from "../types/datasource";

const STAGE_LABELS: Record<ImportStage, string> = {
  DOWNLOAD: "下载",
  VALIDATE: "校验",
  PARSE: "解析",
  INGEST: "入库",
  COMPLETE: "完成",
};

export function presentImportStage(stage: ImportStage | null): string {
  return stage === null ? "未测量" : STAGE_LABELS[stage];
}

export interface ImportProgressView {
  /** 是否有可用于计算百分比的分母。 */
  measured: boolean;
  /** 展示文案：测量过是 "已处理 / 总数"，未测量是"未测量"。 */
  label: string;
  /** 百分比（0-100）；未测量为 null，绝不显示 0%。 */
  percent: number | null;
}

/**
 * 进度口径：``processed_items`` 与 ``total_items`` 都为非空且总数大于 0
 * 才算"已测量"。任何一侧为 null（存量/尚未解析）都显示"未测量"。
 */
export function presentImportProgress(
  processed: number | null,
  total: number | null,
): ImportProgressView {
  if (processed === null || total === null || total <= 0) {
    return { measured: false, label: "未测量", percent: null };
  }
  const clamped = Math.min(processed, total);
  return {
    measured: true,
    label: `${processed} / ${total}`,
    percent: Math.round((clamped / total) * 100),
  };
}
