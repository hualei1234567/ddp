/**
 * 标注结果解析的对外入口。
 *
 * 原文件 840 行，超过 `CLAUDE.md` 的 800 行阈值。按业务簇拆为四份：
 *
 * - `annotationParseBase`：共享读取工具（JSON 值、数组、标签、证据）
 * - `annotationRecordParsers`：标注记录与提交
 * - `annotationReviewParsers`：复核与仲裁
 * - `annotationQualityParsers`：黄金集、质检样本、IAA 报告
 *
 * 本文件只做 re-export，保持既有 `from "./annotationResultParsers"` 调用点不变。
 * 新增解析器应加在对应业务簇里，不要往这里堆。
 */

export {
  parseAnnotationRecord,
  parseAnnotationRecordPage,
  parseAnnotationSubmission,
  parseAnnotationSubmissionPage,
} from "./annotationRecordParsers";
export {
  parseAnnotationArbitration,
  parseAnnotationArbitrationPage,
  parseAnnotationReview,
  parseAnnotationReviewPage,
} from "./annotationReviewParsers";
export {
  parseGoldSet,
  parseGoldSetPage,
  parseIAAReport,
  parseIAAReportPage,
  parseQualitySample,
  parseQualitySamplePage,
} from "./annotationQualityParsers";
