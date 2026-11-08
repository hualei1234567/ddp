/**
 * 集成测试仅使用 Node 内置模块；前端运行时代码不依赖 Node。
 * 项目未引入 @types/node，这里只声明实际用到的 `execFileSync`。
 */
declare module "node:child_process" {
  export interface ExecFileSyncOptions {
    env?: Record<string, string | undefined>;
    stdio?: "pipe" | "inherit" | "ignore";
  }

  export function execFileSync(
    file: string,
    args?: readonly string[],
    options?: ExecFileSyncOptions,
  ): Uint8Array;
}
