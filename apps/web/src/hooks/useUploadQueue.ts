import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CompleteUploadInput,
  UploadPartSummary,
  UploadSession,
} from "../types/collection";
import { ApiError } from "../services/api";
import { createIdempotencyKey } from "../services/idempotency";
import {
  computePartChecksum,
  hashFileSha256,
  planFileParts,
  putUploadPart,
  UploadTransportError,
} from "../services/uploadTransfer";
import { uploadsApi } from "../services/uploads";
import {
  createUploadQueueItem,
  deriveMissingPartNumbers,
  describeUploadFailure,
  guessFormatFromFilename,
  type UploadQueueItem,
  type UploadQueueStatus,
} from "../services/uploadQueue";

interface UseUploadQueueOptions {
  projectId: string;
  taskId?: string | null;
  batchId?: string | null;
}

export interface UseUploadQueueResult {
  items: UploadQueueItem[];
  addFiles: (files: File[]) => void;
  pause: (itemId: string) => void;
  resume: (itemId: string) => void;
  retry: (itemId: string) => void;
  cancel: (itemId: string) => void;
  refreshVerification: (itemId: string) => void;
  remove: (itemId: string) => void;
}

const PART_RETRY_LIMIT = 3;

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new UploadTransportError("上传已取消", {
        retryable: false,
        code: "aborted",
      }));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(
        new UploadTransportError("上传已取消", {
          retryable: false,
          code: "aborted",
        }),
      );
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function verificationStatusOf(
  session: UploadSession,
): UploadQueueStatus {
  if (session.status === "EXPIRED") {
    return "expired";
  }
  if (session.status === "FAILED" || session.status === "ABORTED") {
    return "failed";
  }
  if (session.status === "COMPLETED") {
    if (session.verification_status === "REJECTED") {
      return "failed";
    }
    if (session.verification_status === "VERIFIED") {
      return session.deduplicated ? "deduplicated" : "registered";
    }
    return "verifying";
  }
  return "verifying";
}

function replacePart(
  parts: UploadPartSummary[],
  next: UploadPartSummary,
): UploadPartSummary[] {
  const map = new Map(parts.map((part) => [part.part_number, part]));
  map.set(next.part_number, next);
  return [...map.values()].sort((left, right) => left.part_number - right.part_number);
}

/**
 * 上传队列状态机。
 *
 * - 分片顺序上传，失败只重试失败分片；已确认分片不重传。
 * - 暂停只中止本地请求，不删除本地文件；恢复时先向服务端确认断点。
 * - “上传完成”只进入服务端校验中，校验通过后才显示已登记。
 * - 会话过期、版本冲突、403 分别给出新建会话/刷新/权限恢复路径。
 */
export function useUploadQueue({
  projectId,
  taskId = null,
  batchId = null,
}: UseUploadQueueOptions): UseUploadQueueResult {
  const [items, setItems] = useState<UploadQueueItem[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const controllersRef = useRef(new Map<string, AbortController>());

  const patchItem = useCallback(
    (
      itemId: string,
      patch:
        | Partial<UploadQueueItem>
        | ((item: UploadQueueItem) => Partial<UploadQueueItem>),
    ) => {
      setItems((previous) =>
        previous.map((item) => {
          if (item.id !== itemId) {
            return item;
          }
          const nextPatch =
            typeof patch === "function" ? patch(item) : patch;
          return { ...item, ...nextPatch, updatedAt: Date.now() };
        }),
      );
    },
    [],
  );

  const getItem = useCallback(
    (itemId: string): UploadQueueItem | undefined =>
      itemsRef.current.find((item) => item.id === itemId),
    [],
  );

  const failItem = useCallback(
    (itemId: string, caught: unknown) => {
      const failure = describeUploadFailure(caught);
      patchItem(itemId, {
        status:
          failure.requiresNewSession &&
          failure.code === "upload_session_expired"
            ? "expired"
            : "failed",
        error: failure,
      });
    },
    [patchItem],
  );

  const finishUpload = useCallback(
    async (
      itemId: string,
      file: File,
      session: UploadSession,
      confirmedParts: UploadPartSummary[],
      signal: AbortSignal,
    ) => {
      if (session.status === "COMPLETED") {
        const status = verificationStatusOf(session);
        patchItem(itemId, {
          status,
          session,
          verificationStatus: session.verification_status,
          deduplicatedAssetId:
            session.deduplicated_asset_id ?? session.asset_id,
          uploadedBytes: file.size,
          parts: session.uploaded_parts,
        });
        return;
      }

      const totalParts = planFileParts(file.size, session.part_size_bytes).length;
      const sortedParts = [...confirmedParts].sort(
        (left, right) => left.part_number - right.part_number,
      );
      if (sortedParts.length !== totalParts) {
        patchItem(itemId, {
          status: "failed",
          error: {
            code: "upload_incomplete",
            summary: "仍有分片未确认，不能完成上传。",
            hint: "刷新断点后仅补传缺失分片。",
            retryable: true,
            requiresRefresh: true,
            requiresNewSession: false,
            forbidden: false,
            cancelled: false,
            missingPartNumbers: deriveMissingPartNumbers(session, totalParts),
          },
        });
        return;
      }

      patchItem(itemId, { status: "hashing", uploadedBytes: file.size });
      const expectedSha256 = await hashFileSha256(file, { signal });
      if (signal.aborted) {
        return;
      }

      const input: CompleteUploadInput = {
        expected_sha256: expectedSha256,
        expected_size_bytes: file.size,
        parts: sortedParts.map((part) => ({
          part_number: part.part_number,
          etag: part.etag,
          size_bytes: part.size_bytes,
          sha256: part.sha256,
        })),
      };
      const item = getItem(itemId);
      const response = await uploadsApi.complete(
        projectId,
        session.upload_session_id,
        input,
        {
          version: session.version,
          idempotencyKey: item
            ? `${item.idempotencyKey}:complete`
            : undefined,
          signal,
        },
      );
      const nextSession = response.upload_session;
      const status = verificationStatusOf(nextSession);
      patchItem(itemId, {
        status,
        session: nextSession,
        verificationStatus: nextSession.verification_status,
        deduplicatedAssetId:
          nextSession.deduplicated_asset_id ??
          response.raw_asset?.asset_id ??
          nextSession.asset_id,
        uploadedBytes: file.size,
        parts: nextSession.uploaded_parts,
      });
    },
    [getItem, patchItem, projectId],
  );

  const uploadParts = useCallback(
    async (
      itemId: string,
      file: File,
      session: UploadSession,
      partNumbers: number[],
      knownParts: UploadPartSummary[],
      signal: AbortSignal,
    ) => {
      const parts = planFileParts(file.size, session.part_size_bytes);
      let confirmed = [...knownParts];
      for (const partNumber of [...partNumbers].sort(
        (left, right) => left - right,
      )) {
        if (signal.aborted) {
          return;
        }
        const current = getItem(itemId);
        if (
          !current ||
          current.status === "paused" ||
          current.status === "cancelled"
        ) {
          return;
        }
        const part = parts.find((entry) => entry.partNumber === partNumber);
        if (!part) {
          throw new UploadTransportError("分片计划与文件大小不一致", {
            retryable: false,
            code: "part_plan_invalid",
          });
        }

        patchItem(itemId, { status: "uploading" });
        const partUrl = await uploadsApi.createPartUrl(
          projectId,
          session.upload_session_id,
          partNumber,
          { signal },
        );
        const blob = file.slice(part.start, part.end);
        const checksum = await computePartChecksum(blob);
        // 对象存储的 x-amz-checksum-sha256 必须由预签名 URL 绑定签名。
        // 服务端未在 partUrl.headers 中返回该校验头时，不能自行追加未签名头，
        // 否则 MinIO/S3 会以签名不匹配拒绝；分片 SHA256 仍通过 ack 提交。
        const headers = { ...partUrl.headers };

        let etag = "";
        let lastError: unknown = null;
        for (let attempt = 1; attempt <= PART_RETRY_LIMIT; attempt += 1) {
          try {
            etag = await putUploadPart(
              { ...partUrl, headers },
              blob,
              globalThis.fetch,
              signal,
            );
            lastError = null;
            break;
          } catch (caught) {
            lastError = caught;
            const retryable =
              caught instanceof UploadTransportError && caught.retryable;
            if (!retryable || attempt >= PART_RETRY_LIMIT || signal.aborted) {
              throw caught;
            }
            await delay(400 * attempt, signal);
          }
        }
        if (lastError) {
          throw lastError;
        }

        const ack = await uploadsApi.ackPart(
          projectId,
          session.upload_session_id,
          partNumber,
          {
            etag,
            size_bytes: part.sizeBytes,
            sha256: checksum.prefixed,
          },
          { signal },
        );
        confirmed = replacePart(confirmed, {
          part_number: ack.part_number,
          size_bytes: ack.size_bytes,
          etag: ack.etag,
          sha256: ack.sha256,
          recorded_at: ack.recorded_at,
        });
        patchItem(itemId, {
          parts: confirmed,
          uploadedBytes: ack.uploaded_bytes,
          uploadedPartCount: ack.uploaded_part_count,
        });
      }

      if (!signal.aborted) {
        await finishUpload(itemId, file, session, confirmed, signal);
      }
    },
    [finishUpload, getItem, patchItem, projectId],
  );

  const startItem = useCallback(
    async (itemId: string, source?: UploadQueueItem) => {
      const item = source ?? getItem(itemId);
      if (!item) {
        return;
      }
      if (!item.file) {
        patchItem(itemId, {
          status: "failed",
          error: {
            code: "file_missing",
            summary: "浏览器未保留本地文件，无法继续上传。",
            hint: "请重新选择同一文件以恢复上传；服务端会按断点只补传缺失分片。",
            retryable: false,
            requiresRefresh: false,
            requiresNewSession: false,
            forbidden: false,
            cancelled: false,
            missingPartNumbers: [],
          },
        });
        return;
      }

      controllersRef.current.get(itemId)?.abort();
      const controller = new AbortController();
      controllersRef.current.set(itemId, controller);
      patchItem(itemId, {
        status: "creating",
        startedAt: item.startedAt ?? Date.now(),
        error: null,
      });

      try {
        const file = item.file;
        const session = await uploadsApi.createSession(
          projectId,
          {
            collection_task_id: taskId,
            batch_id: batchId,
            original_filename: file.name,
            declared_content_type:
              file.type || "application/octet-stream",
            declared_size_bytes: file.size,
            format_hint: guessFormatFromFilename(file.name),
          },
          {
            signal: controller.signal,
            idempotencyKey: item.idempotencyKey,
          },
        );
        if (controller.signal.aborted) {
          return;
        }
        const plan = planFileParts(file.size, session.part_size_bytes);
        const missing = deriveMissingPartNumbers(session, plan.length);
        patchItem(itemId, {
          status: "uploading",
          session,
          totalParts: plan.length,
          uploadedBytes: session.uploaded_bytes,
          uploadedPartCount: session.uploaded_part_count,
          parts: session.uploaded_parts,
        });
        if (missing.length === 0 && session.uploaded_parts.length > 0) {
          await finishUpload(
            itemId,
            file,
            session,
            session.uploaded_parts,
            controller.signal,
          );
          return;
        }
        await uploadParts(
          itemId,
          file,
          session,
          missing,
          session.uploaded_parts,
          controller.signal,
        );
      } catch (caught) {
        if (controller.signal.aborted) {
          return;
        }
        failItem(itemId, caught);
      } finally {
        if (controllersRef.current.get(itemId) === controller) {
          controllersRef.current.delete(itemId);
        }
      }
    },
    [
      batchId,
      failItem,
      finishUpload,
      getItem,
      patchItem,
      projectId,
      taskId,
      uploadParts,
    ],
  );

  /**
   * 会话过期/失败后重开必须使用新的幂等键；否则服务端会按
   * `(project_id,user_id,key)` 重放旧会话，导致“新建会话”无效。
   */
  const restartWithNewSession = useCallback(
    (item: UploadQueueItem) => {
      const fresh: UploadQueueItem = {
        ...item,
        status: "waiting",
        session: null,
        parts: [],
        uploadedBytes: 0,
        uploadedPartCount: 0,
        totalParts: null,
        error: null,
        verificationStatus: null,
        deduplicatedAssetId: null,
        idempotencyKey: createIdempotencyKey(),
        startedAt: null,
      };
      patchItem(item.id, fresh);
      void startItem(item.id, fresh);
    },
    [patchItem, startItem],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      const newItems: UploadQueueItem[] = [];
      for (const file of files) {
        const id = createIdempotencyKey();
        const item = createUploadQueueItem({
          id,
          file,
          fileName: file.name,
          sizeBytes: file.size,
          formatHint: guessFormatFromFilename(file.name),
          idempotencyKey: createIdempotencyKey(),
        });
        if (file.size < 1 || file.name.trim() === "") {
          item.status = "failed";
          item.error = {
            code: "file_invalid",
            summary: "文件为空或文件名无效。",
            hint: "请重新选择非空文件；文件名只用于展示，服务端会重新校验内容。",
            retryable: false,
            requiresRefresh: false,
            requiresNewSession: false,
            forbidden: false,
            cancelled: false,
            missingPartNumbers: [],
          };
        }
        newItems.push(item);
      }
      setItems((previous) => [...newItems, ...previous]);
      for (const item of newItems) {
        if (item.status === "waiting") {
          void startItem(item.id, item);
        }
      }
    },
    [startItem],
  );

  const pause = useCallback(
    (itemId: string) => {
      controllersRef.current.get(itemId)?.abort();
      controllersRef.current.delete(itemId);
      patchItem(itemId, (item) =>
        item.status === "uploading" || item.status === "creating" ||
        item.status === "hashing"
          ? { status: "paused" }
          : {},
      );
    },
    [patchItem],
  );

  const resume = useCallback(
    async (itemId: string) => {
      const item = getItem(itemId);
      if (!item) {
        return;
      }
      if (!item.file) {
        patchItem(itemId, {
          status: "failed",
          error: {
            code: "file_missing",
            summary: "浏览器未保留本地文件，无法续传。",
            hint: "请重新选择同一文件；已确认分片仍以服务端记录为准。",
            retryable: false,
            requiresRefresh: false,
            requiresNewSession: false,
            forbidden: false,
            cancelled: false,
            missingPartNumbers: [],
          },
        });
        return;
      }
      if (
        !item.session ||
        item.session.status === "EXPIRED" ||
        item.session.status === "FAILED" ||
        item.session.status === "ABORTED"
      ) {
        restartWithNewSession(item);
        return;
      }

      controllersRef.current.get(itemId)?.abort();
      const controller = new AbortController();
      controllersRef.current.set(itemId, controller);
      patchItem(itemId, { status: "uploading", error: null });
      try {
        const session = await uploadsApi.getSession(
          projectId,
          item.session.upload_session_id,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) {
          return;
        }
        const plan = planFileParts(item.file.size, session.part_size_bytes);
        const missing = deriveMissingPartNumbers(session, plan.length);
        patchItem(itemId, {
          session,
          totalParts: plan.length,
          uploadedBytes: session.uploaded_bytes,
          uploadedPartCount: session.uploaded_part_count,
          parts: session.uploaded_parts,
        });
        if (missing.length === 0) {
          await finishUpload(
            itemId,
            item.file,
            session,
            session.uploaded_parts,
            controller.signal,
          );
        } else {
          await uploadParts(
            itemId,
            item.file,
            session,
            missing,
            session.uploaded_parts,
            controller.signal,
          );
        }
      } catch (caught) {
        if (controller.signal.aborted) {
          return;
        }
        failItem(itemId, caught);
      } finally {
        if (controllersRef.current.get(itemId) === controller) {
          controllersRef.current.delete(itemId);
        }
      }
    },
    [
      failItem,
      finishUpload,
      getItem,
      patchItem,
      projectId,
      restartWithNewSession,
      uploadParts,
    ],
  );

  const retry = useCallback(
    (itemId: string) => {
      const item = getItem(itemId);
      if (!item) {
        return;
      }
      if (
        item.error?.requiresNewSession ||
        !item.session ||
        item.session.status === "EXPIRED"
      ) {
        restartWithNewSession(item);
        return;
      }
      void resume(itemId);
    },
    [getItem, restartWithNewSession, resume],
  );

  const cancel = useCallback(
    (itemId: string) => {
      const item = getItem(itemId);
      controllersRef.current.get(itemId)?.abort();
      controllersRef.current.delete(itemId);
      patchItem(itemId, { status: "cancelled" });
      if (item?.session && !isSessionTerminal(item.session)) {
        void uploadsApi
          .abort(projectId, item.session.upload_session_id, {
            version: item.session.version,
            reason: "用户取消上传（本地文件保留）",
            idempotencyKey: `${item.idempotencyKey}:abort`,
          })
          .catch(() => {
            // 中止请求失败不回滚本地取消状态；服务端会话由后台清理。
          });
      }
    },
    [getItem, patchItem, projectId],
  );

  const refreshVerification = useCallback(
    async (itemId: string) => {
      const item = getItem(itemId);
      if (!item?.session) {
        return;
      }
      try {
        const session = await uploadsApi.getSession(
          projectId,
          item.session.upload_session_id,
        );
        const status = verificationStatusOf(session);
        patchItem(itemId, {
          status,
          session,
          verificationStatus: session.verification_status,
          deduplicatedAssetId:
            session.deduplicated_asset_id ?? session.asset_id,
          error:
            status === "failed"
              ? {
                  code: session.verification_error_code ?? session.failure_code ?? "verification_failed",
                  summary:
                    session.failure_summary ?? "服务端校验未通过。",
                  hint: "请根据错误码检查文件内容；如需重新上传，请新建会话。",
                  retryable: false,
                  requiresRefresh: false,
                  requiresNewSession: true,
                  forbidden: false,
                  cancelled: false,
                  missingPartNumbers: [],
                }
              : null,
        });
      } catch (caught) {
        if (caught instanceof ApiError && caught.kind === "aborted") {
          return;
        }
        failItem(itemId, caught);
      }
    },
    [failItem, getItem, patchItem, projectId],
  );

  const remove = useCallback((itemId: string) => {
    controllersRef.current.get(itemId)?.abort();
    controllersRef.current.delete(itemId);
    setItems((previous) => previous.filter((item) => item.id !== itemId));
  }, []);

  useEffect(() => {
    const controllers = controllersRef.current;
    return () => {
      for (const controller of controllers.values()) {
        controller.abort();
      }
      controllers.clear();
    };
  }, []);

  return {
    items,
    addFiles,
    pause,
    resume,
    retry,
    cancel,
    refreshVerification,
    remove,
  };
}

function isSessionTerminal(session: UploadSession): boolean {
  return (
    session.status === "COMPLETED" ||
    session.status === "ABORTED" ||
    session.status === "EXPIRED" ||
    session.status === "FAILED"
  );
}
