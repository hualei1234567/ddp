/**
 * QC 证据定位的展示与换算（V51-010 / FR-025）。
 *
 * ## 这一层解决的唯一问题
 *
 * 质量结论里的证据时间是**绝对纳秒字符串**（例如 `1763510400000000000`），
 * 而播放器与时间轴吃的是**相对 Episode 起点的秒数**。换算一旦出错，界面就会
 * 跳到错误的位置——那比"没有定位"更糟，因为用户会以为看到了证据。
 *
 * 因此换算规则集中在这里，并且：
 * - 用 `BigInt` 做纳秒减法：绝对纳秒约 `1.8e18`，`Number` 相减会丢精度；
 * - 换算失败返回 `null` 而不是 0：0 是合法的"Episode 起点"，不能兼作失败值；
 * - 区间必须落在 Episode 内才可高亮：越界部分裁剪到边界，完全不相交时
 *   如实返回 `null`（不画一个 Episode 外的高亮块）。
 *
 * ## 不做什么
 *
 * 不推断证据属于哪一路流：`stream_id` 由服务端给出，前端只负责比较与高亮。
 * 不做"找不到流就高亮全部"这类兜底——那会把"无法定位"伪装成"已定位"。
 */

import type {
  QualityEvidenceLocation,
  QualityEvidenceTimeRange,
  QualityRunFinding,
} from "../types/qualityRun";

/** 证据定位在界面上的三类可用程度。 */
export const EVIDENCE_LOCATABILITY_VALUES = [
  "NONE",
  "FIELD_ONLY",
  "TIME_RANGE",
] as const;
export type EvidenceLocatability =
  (typeof EVIDENCE_LOCATABILITY_VALUES)[number];

/** 一条结论在播放器/时间轴上可执行的动作。 */
export interface EvidenceJumpTarget {
  /** 播放器跳转位置（相对 Episode 起点的秒）；无时间维度时为 null。 */
  seekSeconds: number | null;
  /** 时间轴高亮区间（相对秒）；起止都有效时给出。 */
  highlight: { startSeconds: number; endSeconds: number } | null;
  /** 要高亮的媒体流（机位）；服务端未给出时为 null。 */
  streamId: string | null;
  /** 证据指向的字段路径；服务端未给出时为 null。 */
  fieldPath: string | null;
  /** 是否至少有一个维度可用于定位。 */
  locatable: boolean;
}

const NOT_LOCATABLE: EvidenceJumpTarget = {
  seekSeconds: null,
  highlight: null,
  streamId: null,
  fieldPath: null,
  locatable: false,
};

/** 十进制纳秒字符串 → bigint；非法输入返回 null（不抛错，由调用方决定展示）。 */
export function parseNanoseconds(value: string | number | null): bigint | null {
  if (value === null) {
    return null;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || !Number.isInteger(value)) {
      return null;
    }
    // number 通道只用于调用方已确认安全的场景；超出安全整数范围说明它本就是
    // 大数，必须走字符串通道，这里显式拒绝而不是静默丢精度。
    if (!Number.isSafeInteger(value)) {
      return null;
    }
    return BigInt(value);
  }
  const text = value.trim();
  if (!/^-?[0-9]+$/.test(text)) {
    return null;
  }
  return BigInt(text);
}

/**
 * 绝对纳秒区间 → 相对 Episode 起点的秒区间。
 *
 * Episde 边界与证据区间不相交时返回 `null`：一个完全落在 Episode 之外的
 * 区间无法在时间轴上高亮，画出来会是一个贴边的空块，读起来像"证据在第一帧"。
 */
export function relativeRangeFromNs(
  range: QualityEvidenceTimeRange | null,
  episodeStartNs: string | number | null,
  episodeEndNs: string | number | null,
): { startSeconds: number; endSeconds: number } | null {
  if (range === null) {
    return null;
  }
  const evidenceStart = parseNanoseconds(range.start_time_ns);
  const evidenceEnd = parseNanoseconds(range.end_time_ns);
  const episodeStart = parseNanoseconds(episodeStartNs);
  const episodeEnd = parseNanoseconds(episodeEndNs);
  if (
    evidenceStart === null ||
    evidenceEnd === null ||
    episodeStart === null ||
    episodeEnd === null
  ) {
    return null;
  }
  if (evidenceEnd <= evidenceStart || episodeEnd <= episodeStart) {
    return null;
  }
  const clippedStart = evidenceStart < episodeStart ? episodeStart : evidenceStart;
  const clippedEnd = evidenceEnd > episodeEnd ? episodeEnd : evidenceEnd;
  if (clippedEnd <= clippedStart) {
    return null;
  }
  // 只在这一步转 Number：差值已裁剪到 Episode 时长内（现实时长远小于 2^53 ns）。
  const startSeconds = Number(clippedStart - episodeStart) / 1e9;
  const endSeconds = Number(clippedEnd - episodeStart) / 1e9;
  if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds)) {
    return null;
  }
  return { startSeconds, endSeconds };
}

/**
 * 把一条结论的证据定位换算成界面可直接消费的跳转目标。
 *
 * 没有 `evidence_location`、或只有字段路径时，`seekSeconds` / `highlight`
 * 都是 null 且 `locatable` 仍可能是 true（字段定位也可用于"跳到审核表单"），
 * 但播放器不会被要求跳转。
 */
export function buildEvidenceJumpTarget(
  location: QualityEvidenceLocation | null,
  episodeStartNs: string | number | null,
  episodeEndNs: string | number | null,
): EvidenceJumpTarget {
  if (location === null) {
    return NOT_LOCATABLE;
  }
  const highlight = relativeRangeFromNs(
    location.time_range,
    episodeStartNs,
    episodeEndNs,
  );
  const locatable =
    highlight !== null ||
    location.stream_id !== null ||
    location.field_path !== null;
  return {
    seekSeconds: highlight?.startSeconds ?? null,
    highlight,
    streamId: location.stream_id,
    fieldPath: location.field_path,
    locatable,
  };
}

/**
 * 结论是否"值得定位"：只有未通过或规则执行失败的结论才需要证据。
 *
 * 通过的结论也带定位（例如同步通过的 Episode 区间），但给每条 PASS 都渲染
 * 一个"查看证据"按钮会把真正需要人看的问题淹没。判断只依据服务端给出的
 * `status`，前端不重新判定规则。
 */
export function findingNeedsEvidence(finding: QualityRunFinding): boolean {
  return finding.status === "FAIL" || finding.status === "ERROR";
}

/**
 * 证据可定位程度的文案。
 *
 * `NONE` 必须显式说明"无法定位"，不能什么都不显示——用户会以为功能坏了，
 * 或者更糟：以为定位到了默认位置。
 */
export function describeEvidenceLocatability(
  target: EvidenceJumpTarget,
): string {
  if (!target.locatable) {
    return "该结论没有可定位的证据（规则未给出时间区间、流或字段）";
  }
  const parts: string[] = [];
  if (target.highlight !== null) {
    parts.push(
      `时间轴区间 ${formatRelativeSeconds(target.highlight.startSeconds)} – ${formatRelativeSeconds(target.highlight.endSeconds)}`,
    );
  }
  if (target.streamId !== null) {
    parts.push(`媒体流 ${target.streamId}`);
  }
  if (target.fieldPath !== null) {
    parts.push(`字段 ${target.fieldPath}`);
  }
  return parts.join(" · ");
}

/** 相对秒的短格式（`mm:ss.s`）；与播放器时钟保持同一口径。 */
export function formatRelativeSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) {
    return "--:--.-";
  }
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** 纳秒（秒通道）换算辅助：给需要"秒级判断"的调用方用。 */
export function isEvidenceRangeWithin(
  range: QualityEvidenceTimeRange | null,
  episodeStartNs: string | number | null,
  episodeEndNs: string | number | null,
): boolean {
  return relativeRangeFromNs(range, episodeStartNs, episodeEndNs) !== null;
}

// ---------------------------------------------------------------------------
// 返工上下文（V51-010 / FR-025）
// ---------------------------------------------------------------------------

/** 建议原因的最大长度：与服务端既有 reason 上限（2000）保持一致。 */
export const REWORK_REASON_MAX_LENGTH = 2000;

/**
 * 返工上下文：从一条 QC 结论派生、**只用于预填表单**。
 *
 * 刻意不进契约、不发请求：它是 UI 层的建议文本，服务端只接受既有
 * `reason`（≤2000 字符）与既有 `require-rework` 动作。把它变成 API 入参
 * 会让"QC 结论"成为审核流转的一部分，违反蓝图 §5（QC 不代替 Reviewer）。
 */
export interface ReworkContext {
  /** 建议的返工原因（中文可读，含规则 id/version 与证据位置）。 */
  reason: string;
  /** 来源结论 ID，供调用方去重或回溯。 */
  findingId: string;
  /** 规则 id + 版本，便于人核对"当时是哪一版规则判的"。 */
  ruleId: string;
  ruleVersion: string;
}

/**
 * 从结论派生返工上下文。
 *
 * 只放**可核对的事实**：规则 ID/版本、严重级别、字段路径、时间区间与阈值。
 * 不写"建议删除数据""这条一定错了"这类判断——QC 提供证据，不代替 Reviewer
 * 下结论（蓝图 §5）。
 */
export function buildReworkContext(
  finding: QualityRunFinding,
  evidenceLabel: string,
): ReworkContext {
  const parts: string[] = [
    `QC 规则 ${finding.rule_id}@${finding.rule_version} 判定 ${finding.status}`,
    `严重级别 ${finding.severity}`,
    `结论：${finding.message}`,
  ];
  if (evidenceLabel.trim() !== "") {
    parts.push(`证据位置：${evidenceLabel}`);
  }
  const reason = parts.join("；");
  return {
    reason:
      reason.length > REWORK_REASON_MAX_LENGTH
        ? `${reason.slice(0, REWORK_REASON_MAX_LENGTH - 1)}…`
        : reason,
    findingId: finding.finding_id,
    ruleId: finding.rule_id,
    ruleVersion: finding.rule_version,
  };
}

// ---------------------------------------------------------------------------
// require-rework 的既有可达状态（与后端状态机同源，见
// `domain.annotation.state_machines._SUBMISSION_RULES`）
// ---------------------------------------------------------------------------

/**
 * `require-rework` 在既有状态机里可达的提交状态。
 *
 * **唯一权威在后端**：这里只用于"按钮是否置灰"的提前提示，避免用户点了
 * 才收到 409。任何状态迁移仍由服务端裁决——前端不得据此自行推进状态。
 */
export const REWORK_REACHABLE_SUBMISSION_STATUSES = [
  "REJECTED",
  "RESOLVED_REJECTED",
  "RESOLVED_REWORK",
] as const;
