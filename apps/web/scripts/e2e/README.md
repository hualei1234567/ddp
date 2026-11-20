# 前端关键路径 E2E（DDP-V21-N8-007）

使用官方 `@playwright/test`（版本固定在 `apps/web/package.json`），覆盖三条关键路径：
**上传**、**审核**、**动作条**。

## 无环境时明确跳过

```bash
npm run e2e
# SKIPPED: 未提供 DDP_E2E_BASE_URL
# 退出码 0，但报告里是 skipped 而不是 passed —— 不会把"没跑"当成"通过"。
```

## 一键本地全栈

需要调用方先启动 MinIO，并准备 `backend/.venv` 与前端依赖。本脚本**不负责启动或停止 MinIO**；可复用仓库中已固定 digest 的 Compose 服务：

```bash
docker compose up -d --wait minio
cd apps/web
DDP_E2E_MINIO_PORT=9000 npm run e2e:local
```

默认使用 `backend/.venv/bin/python`；CI 或其他隔离环境可通过
`DDP_E2E_PYTHON=/path/to/python` 指定解释器。脚本会把
`backend:data-platform`（绝对路径）注入 `PYTHONPATH`。

脚本会：在已有回环 MinIO 创建临时桶 → 起临时 SQLite 并迁移 →
灌种子数据（`seed_e2e.py`）→ 起临时后端、上传校验 Worker 与 Vite →
跑 Playwright → 清理临时进程与文件。MinIO 服务及其数据卷由调用方管理，
不在清理范围内。

## 对接已有环境

```bash
DDP_E2E_BASE_URL=http://127.0.0.1:8088 \
DDP_E2E_USER_ID=<项目成员 UUID> \
DDP_E2E_PROJECT_ID=<项目 UUID> \
DDP_E2E_TASK_ID=<采集任务 UUID> \
DDP_E2E_IMPORT_ID=<导入任务 UUID> \
npm run e2e
```

## 安全边界

- 身份通过 `X-User-Id` 头注入，**只在** `DDP_ENV=local` + 回环地址时被后端接受；
- 不读取仓库 `.env`，不写任何真实凭据；
- 种子数据只写临时库，跑完即删。

## 组件交互测试（jsdom）

除 E2E 外，`*.dom.test.tsx` 走 jsdom + Testing Library（见 `vite.config.ts` 的
`environmentMatchGlobs`）；其余测试仍是 node 环境，避免把整个套件拖进浏览器模拟。
