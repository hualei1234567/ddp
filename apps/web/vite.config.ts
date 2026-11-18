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
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "node",
  },
});
