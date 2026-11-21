/**
 * OBJECT_TRACK 草稿的结构化解析与序列化（V31-023）。
 *
 * 草稿在表单里始终是 JSON 字符串（既有提交/校验链路依赖该契约），本模块只
 * 在「结构化编辑器」与字符串之间做无损转换：
 *
 * - 解析失败（非 JSON / 非数组 / 行缺整数 ns）一律返回错误，**不猜值、不修正**；
 * - 行里除 `start_time_ns` / `end_time_ns` 之外的字段原样保留（例如
 *   `track_id` / `bbox`），编辑器不会丢弃用户数据；
 * - 序列化前由 `objectTrackRowIssue` 校验，调用方只在全部合法时写回。
 */

export interface ObjectTrackRow {
  /** 表单里保持字符串，避免输入过程中的中间态被 Number 吃掉。 */
  startNs: string;
  endNs: string;
  /** 原 JSON 中除起止时间外的字段，原样回写。 */
  extra: Record<string, unknown>;
}

export interface ObjectTrackParseResult {
  rows: ObjectTrackRow[];
  /** null 表示可结构化；非 null 表示只能退回原文编辑。 */
  error: string | null;
}

function isIntegerLike(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

export function parseObjectTrackDraft(text: string): ObjectTrackParseResult {
  if (text.trim() === "") {
    return { rows: [], error: null };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { rows: [], error: "OBJECT_TRACK 必须是合法 JSON 数组" };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return { rows: [], error: "OBJECT_TRACK 必须是非空数组" };
  }
  const rows: ObjectTrackRow[] = [];
  for (const item of parsed) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      return { rows: [], error: "OBJECT_TRACK 每个元素必须是对象" };
    }
    const record = item as Record<string, unknown>;
    const { start_time_ns: start, end_time_ns: end, ...extra } = record;
    if (!isIntegerLike(start) || !isIntegerLike(end)) {
      return {
        rows: [],
        error: "OBJECT_TRACK 每项必须包含整数 start_time_ns 与 end_time_ns",
      };
    }
    rows.push({ startNs: String(start), endNs: String(end), extra });
  }
  return { rows, error: null };
}

export function serializeObjectTrackDraft(rows: readonly ObjectTrackRow[]): string {
  return JSON.stringify(
    rows.map((row) => ({
      ...row.extra,
      start_time_ns: Number(row.startNs),
      end_time_ns: Number(row.endNs),
    })),
  );
}

function parseInteger(text: string): number | null {
  if (!/^-?\d+$/.test(text.trim())) {
    return null;
  }
  const value = Number(text.trim());
  return Number.isSafeInteger(value) ? value : null;
}

function specNumber(spec: Record<string, unknown>, key: string): number | null {
  const raw = spec[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/** 行级校验；返回 null 表示可写回。与后端 `_require_time_value` 口径一致。 */
export function objectTrackRowIssue(
  row: ObjectTrackRow,
  spec: Record<string, unknown> = {},
): string | null {
  const start = parseInteger(row.startNs);
  const end = parseInteger(row.endNs);
  if (start === null || end === null) {
    return "开始与结束必须是整数纳秒";
  }
  if (start >= end) {
    return "开始时间必须小于结束时间";
  }
  const duration = end - start;
  const minDuration = specNumber(spec, "min_duration_ns");
  if (minDuration !== null && duration < minDuration) {
    return `时间片段短于最小允许时长 ${minDuration} ns`;
  }
  const maxDuration = specNumber(spec, "max_duration_ns");
  if (maxDuration !== null && duration > maxDuration) {
    return `时间片段超过最大允许时长 ${maxDuration} ns`;
  }
  return null;
}
