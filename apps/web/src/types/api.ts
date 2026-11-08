/**
 * 与 `packages/contracts/schemas/error-envelope.json` 保持一致。
 * 前端只读取契约，不修改契约文件。
 *
 * `details` 是阶段 2 草案的可选扩展（字段级错误、missing_part_numbers 等），
 * 前端只按稳定 code 分支，不把 details 当作权威业务状态。
 */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string | null;
    details?: Record<string, unknown> | null;
  };
}

/** 阶段 0/1/2 统一游标分页响应：`{ items, next_cursor }`。 */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}
