import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // 静态产物不能放在 `assets/`：产品里有一个真实的页面路由 `/assets`
    // （数据资产清单）。Vite 默认把 JS/CSS 输出到 dist/assets/，nginx 会把
    // `/assets` 当成静态目录处理并 301 到 `/assets/`，页面直接 403——
    // 菜单点进去是 Forbidden，而不是清单。换成 `static/` 两个命名空间分开。
    assetsDir: "static",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // E2E 需要把前端代理指向临时后端端口；默认仍是本地 8000，
        // 避免改变现有开发流程。
        target: process.env.DDP_WEB_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: process.env.DDP_WEB_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    // 默认走 node 环境：现有大量纯函数/服务层测试不依赖 DOM。
    // 只有显式命名为 `*.dom.test.tsx` 的组件交互测试切到 jsdom，
    // 避免把整个测试套件拖进浏览器模拟环境（慢且掩盖真实依赖）。
    environment: "node",
    environmentMatchGlobs: [["**/*.dom.test.{ts,tsx}", "jsdom"]],
    setupFiles: ["./src/test/setupDom.ts"],
    // E2E（Playwright）与真实环境集成脚本不是 vitest 单测：
    // 误收集会让 `npm test` 因缺少浏览器/后端而整片失败。
    exclude: ["**/node_modules/**", "**/dist/**", "**/e2e/**", "**/*.live.test.mjs"],
  },
});
