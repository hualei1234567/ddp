/**
 * 与 `packages/contracts/schemas/error-envelope.json` 保持一致。
 * 前端只读取契约，不修改契约文件。
 */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string | null;
  };
}
