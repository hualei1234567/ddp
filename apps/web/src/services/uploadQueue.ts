import type {
  AssetFormat,
  UploadPartSummary,
  UploadSession,
  UploadVerificationStatus,
} from "../types/collection";
import { ApiError } from "./api";
import { UploadTransportError } from "./uploadTransfer";

export type UploadQueueStatus =
  | "waiting"
  | "creating"
  | "uploading"
  | "hashing"
  | "verifying"
  | "registered"
  | "deduplicated"
  | "paused"
  | "failed"
  | "cancelled"
  | "expired";

export interface UploadFailure {
  code: string;
  summary: string;
  hint: string;
  retryable: boolean;
  requiresRefresh: boolean;
  requiresNewSession: boolean;
  forbidden: boolean;
  cancelled: boolean;
  missingPartNumbers: number[];
  requestId?: string | null;
}

export interface UploadQueueItem {
  id: string;
  /** 选中的本地文件；刷新页面后浏览器不会保留 File，续传需用户重新选择。 */
  file: File | null;
  fileName: string;
  sizeBytes: number;
  status: UploadQueueStatus;
  uploadedBytes: number;
  uploadedPartCount: number;
  totalParts: number | null;
  session: UploadSession | null;
  parts: UploadPartSummary[];
  error: UploadFailure | null;
  verificationStatus: UploadVerificationStatus | null;
  deduplicatedAssetId: string | null;
  formatHint: AssetFormat | null;
  idempotencyKey: string;
  startedAt: number | null;
  updatedAt: number;
}

export interface UploadQueueItemInput {
  id: string;
  fileName: string;
  sizeBytes: number;
  formatHint?: AssetFormat | null;
  idempotencyKey: string;
  file?: File | null;
  now?: number;
}

export function createUploadQueueItem(
  input: UploadQueueItemInput,
): UploadQueueItem {
  const now = input.now ?? Date.now();
  return {
    id: input.id,
    file: input.file ?? null,
    fileName: input.fileName,
    sizeBytes: input.sizeBytes,
    status: "waiting",
    uploadedBytes: 0,
    uploadedPartCount: 0,
    totalParts: null,
    session: null,
    parts: [],
    error: null,
    verificationStatus: null,
    deduplicatedAssetId: null,
    formatHint: input.formatHint ?? null,
    idempotencyKey: input.idempotencyKey,
    startedAt: null,
    updatedAt: now,
  };
}

const ACTIVE_STATUSES = new Set<UploadQueueStatus>([
  "creating",
  "uploading",
  "hashing",
]);

const TERMINAL_STATUSES = new Set<UploadQueueStatus>([
  "registered",
  "deduplicated",
  "cancelled",
]);

export function isUploadActive(status: UploadQueueStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

export function isUploadTerminal(status: UploadQueueStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function uploadProgressPercent(item: UploadQueueItem): number | null {
  if (item.sizeBytes <= 0) {
    return null;
  }
  const ratio = item.uploadedBytes / item.sizeBytes;
  return Math.max(0, Math.min(100, Math.round(ratio * 100)));
}

export function canPauseUpload(item: UploadQueueItem): boolean {
  return item.status === "uploading" || item.status === "creating";
}

export function canResumeUpload(item: UploadQueueItem): boolean {
  return item.status === "paused" || item.status === "failed";
}

export function canRetryUpload(item: UploadQueueItem): boolean {
  return (
    item.status === "failed" &&
    item.error !== null &&
    item.error.retryable
  );
}

export function canCancelUpload(item: UploadQueueItem): boolean {
  return !isUploadTerminal(item.status) && item.status !== "verifying";
}

export function canRefreshVerification(item: UploadQueueItem): boolean {
  return item.status === "verifying";
}

export function formatBytes(bytes: number | null | undefined): string {
  if (
    typeof bytes !== "number" ||
    !Number.isFinite(bytes) ||
    bytes < 0
  ) {
    return "—";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

export function uploadSpeedBytesPerSecond(
  item: UploadQueueItem,
  now: number,
): number | null {
  if (item.startedAt === null || item.uploadedBytes <= 0) {
    return null;
  }
  const elapsedSeconds = (now - item.startedAt) / 1000;
  if (elapsedSeconds <= 0) {
    return null;
  }
  return item.uploadedBytes / elapsedSeconds;
}

export function formatRemainingTime(
  item: UploadQueueItem,
  now: number,
): string {
  const speed = uploadSpeedBytesPerSecond(item, now);
  const remainingBytes = item.sizeBytes - item.uploadedBytes;
  if (speed === null || speed <= 0 || remainingBytes <= 0) {
    return "—";
  }
  const seconds = Math.ceil(remainingBytes / speed);
  if (seconds < 60) {
    return `约 ${seconds} 秒`;
  }
  return `约 ${Math.ceil(seconds / 60)} 分钟`;
}

/** 已确认分片之外的分片号；优先使用服务端下发的 missing_part_numbers。 */
export function deriveMissingPartNumbers(
  session: UploadSession,
  totalParts: number,
): number[] {
  if (session.missing_part_numbers.length > 0) {
    return [...session.missing_part_numbers].sort((left, right) => left - right);
  }
  const uploaded = new Set(
    session.uploaded_parts.map((part) => part.part_number),
  );
  const missing: number[] = [];
  for (let partNumber = 1; partNumber <= totalParts; partNumber += 1) {
    if (!uploaded.has(partNumber)) {
      missing.push(partNumber);
    }
  }
  return missing;
}

function readMissingParts(
  details: Record<string, unknown> | null,
): number[] {
  const value = details?.missing_part_numbers;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is number =>
      typeof item === "number" && Number.isInteger(item) && item > 0,
  );
}

/**
 * 把接口/传输错误翻译成可展示的四要素：发生了什么、原因、可做什么、错误码。
 * 未知错误不得默认成功，也不承诺自动恢复。
 */
export function describeUploadFailure(caught: unknown): UploadFailure {
  if (caught instanceof UploadTransportError) {
    if (caught.code === "aborted") {
      return {
        code: "aborted",
        summary: "上传已暂停或取消。",
        hint: "本地文件未被删除；继续上传时会先向服务端确认已传分片。",
        retryable: true,
        requiresRefresh: false,
        requiresNewSession: false,
        forbidden: false,
        cancelled: true,
        missingPartNumbers: [],
      };
    }
    return {
      code: caught.code,
      summary: caught.message,
      hint: caught.retryable
        ? "网络或对象存储短暂异常，可重试失败分片；已确认分片不会重传。"
        : "该分片被拒绝，请刷新会话或重新选择文件；不要重复盲传。",
      retryable: caught.retryable,
      requiresRefresh: !caught.retryable,
      requiresNewSession: false,
      forbidden: false,
      cancelled: false,
      missingPartNumbers: [],
    };
  }

  if (caught instanceof ApiError) {
    const code = caught.code ?? `http_${caught.status ?? "unknown"}`;
    const missingPartNumbers = readMissingParts(caught.details);
    if (caught.kind === "aborted") {
      return {
        code: "aborted",
        summary: "请求已取消。",
        hint: "继续上传时会重新向服务端确认断点。",
        retryable: true,
        requiresRefresh: false,
        requiresNewSession: false,
        forbidden: false,
        cancelled: true,
        missingPartNumbers,
      };
    }
    if (caught.isForbidden || caught.isUnauthorized) {
      return {
        code,
        summary: "当前账号无权继续该上传操作。",
        hint: "权限以服务端为准；请切换有权限的账号或联系项目管理员。",
        retryable: false,
        requiresRefresh: false,
        requiresNewSession: false,
        forbidden: true,
        cancelled: false,
        missingPartNumbers,
      };
    }
    if (code === "upload_session_expired" || caught.isGone) {
      return {
        code,
        summary: "上传会话已过期。",
        hint: "请新建上传会话后重新上传；已传分片不承诺保留。",
        retryable: true,
        requiresRefresh: false,
        requiresNewSession: true,
        forbidden: false,
        cancelled: false,
        missingPartNumbers,
      };
    }
    if (code === "version_conflict" || code === "precondition_required") {
      return {
        code,
        summary: "服务端版本已变化，当前请求未生效。",
        hint: "请刷新会话或任务状态后重试；不会覆盖他人修改。",
        retryable: true,
        requiresRefresh: true,
        requiresNewSession: false,
        forbidden: false,
        cancelled: false,
        missingPartNumbers,
      };
    }
    if (code === "upload_incomplete") {
      return {
        code,
        summary:
          missingPartNumbers.length > 0
            ? `仍有 ${missingPartNumbers.length} 个分片未确认。`
            : "分片不完整，服务端未接受完成请求。",
        hint:
          missingPartNumbers.length > 0
            ? `仅重试缺失分片：${missingPartNumbers.join("、")}。`
            : "刷新断点状态后只补传缺失分片。",
        retryable: true,
        requiresRefresh: true,
        requiresNewSession: false,
        forbidden: false,
        cancelled: false,
        missingPartNumbers,
      };
    }
    if (code === "upload_part_conflict" || code === "size_mismatch") {
      return {
        code,
        summary: "分片或文件大小与服务端记录不一致。",
        hint: "刷新会话后重传冲突分片；Hash 不一致才需要整体重传。",
        retryable: true,
        requiresRefresh: true,
        requiresNewSession: false,
        forbidden: false,
        cancelled: false,
        missingPartNumbers,
      };
    }
    if (
      code === "hash_mismatch" ||
      code === "file_signature_mismatch" ||
      code === "file_type_unsupported" ||
      code === "file_too_large" ||
      code === "validation_failed"
    ) {
      return {
        code,
        summary: "文件校验未通过，服务端不会登记为可用资产。",
        hint: "请根据错误码检查文件内容、类型与大小；必要时重新选择文件。",
        retryable: false,
        requiresRefresh: false,
        requiresNewSession: true,
        forbidden: false,
        cancelled: false,
        missingPartNumbers,
      };
    }
    const retryable =
      caught.kind === "network" ||
      caught.kind === "timeout" ||
      code === "rate_limited" ||
      caught.status === 503 ||
      code === "object_storage_unavailable" ||
      code === "dependency_unavailable";
    return {
      code,
      summary: caught.message,
      hint: retryable
        ? "网络或服务暂时异常，可稍后重试；已确认分片不会重传。"
        : "请刷新会话后重试；若持续失败请携带错误码与 request_id 联系支持。",
      retryable,
      requiresRefresh: !retryable,
      requiresNewSession: false,
      forbidden: false,
      cancelled: false,
      missingPartNumbers,
    };
  }

  return {
    code: "unknown_error",
    summary: "上传遇到未识别错误。",
    hint: "请刷新会话后重试；若持续失败请携带 request_id 联系支持。",
    retryable: true,
    requiresRefresh: true,
    requiresNewSession: false,
    forbidden: false,
    cancelled: false,
    missingPartNumbers: [],
  };
}

const FORMAT_BY_EXTENSION: Record<string, AssetFormat> = {
  mcap: "MCAP",
  db3: "ROSBAG2",
  h5: "HDF5",
  hdf5: "HDF5",
  mp4: "MP4",
  mov: "MP4",
  parquet: "PARQUET",
  csv: "CSV",
  json: "JSON",
  zip: "ZIP",
};

/** 扩展名仅作为格式提示；服务端仍以内容签名与解析结果为准。 */
export function guessFormatFromFilename(filename: string): AssetFormat {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  return FORMAT_BY_EXTENSION[extension] ?? "UNKNOWN";
}

const QUEUE_STATUS_LABELS: Record<UploadQueueStatus, string> = {
  waiting: "等待上传",
  creating: "创建会话中",
  uploading: "上传中",
  hashing: "计算整文件 Hash",
  verifying: "服务端校验中",
  registered: "已登记",
  deduplicated: "重复文件（已复用）",
  paused: "已暂停",
  failed: "上传失败",
  cancelled: "已取消",
  expired: "会话已过期",
};

export function getUploadQueueStatusLabel(status: UploadQueueStatus): string {
  return QUEUE_STATUS_LABELS[status];
}

