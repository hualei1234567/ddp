import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 真实后端契约检查：重新调用 FastAPI 路由生成响应，并与已提交快照逐值比较。
 * 后端字段、枚举或序列化漂移时本测试失败。
 *
 * 需要 backend/.venv 或可用的 python3；仅前端 CI 可用
 * `DDP_SKIP_BACKEND_CONTRACT=1` 显式跳过，默认不跳过。
 */
const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(scriptsDir, "..");
const repoRoot = path.resolve(webRoot, "../..");
const backendPython = path.join(repoRoot, "backend", ".venv", "bin", "python");
const python =
  process.env.DDP_CONTRACT_PYTHON ??
  (existsSync(backendPython) ? backendPython : "python3");
const snapshotScript = path.join(scriptsDir, "device_contract_snapshot.py");
const skip = process.env.DDP_SKIP_BACKEND_CONTRACT === "1";

describe.skipIf(skip)("真实后端设备契约快照", () => {
  it(
    "实时响应与已提交快照逐值一致",
    () => {
      let output;
      try {
        output = execFileSync(python, [snapshotScript, "--check"], {
          cwd: webRoot,
          encoding: "utf8",
          env: { ...process.env, DDP_ENV: "local" },
        });
      } catch (error) {
        const stdout =
          error && typeof error === "object" && "stdout" in error
            ? String(error.stdout ?? "")
            : "";
        const stderr =
          error && typeof error === "object" && "stderr" in error
            ? String(error.stderr ?? "")
            : "";
        throw new Error(
          `真实后端契约检查失败（${python}）：\n${stdout}${stderr}`,
        );
      }
      expect(output).toContain("后端设备契约与已提交快照一致");
    },
    60_000,
  );
});
