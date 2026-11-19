#!/usr/bin/env bash
# DDP-V21-N8-007 前端关键路径 E2E 启动器。
#
# 职责：
#   1. 未提供 DDP_E2E_BASE_URL 时**显式跳过**（退出码 0，输出 SKIPPED 原因），
#      绝不静默通过——CI 里看到的是明确的 skip 行，而不是"测试通过"。
#   2. 提供环境时：临时 SQLite + 本机回环 MinIO 桶 + 临时后端 + Vite 预览，
#      跑完 Playwright 后清理全部临时进程与文件。
#
# 不读取仓库 .env，不写任何真实凭据；身份通过 DDP_E2E_USER_ID 注入
# X-User-Id 头，仅对 local + loopback 后端有效。
#
# 用法：
#   # 只验证"无环境时明确跳过"
#   npm run e2e
#
#   # 有环境时（自行起好后端与前端）
#   DDP_E2E_BASE_URL=http://127.0.0.1:8088 \
#   DDP_E2E_USER_ID=88888888-8888-7888-8888-888888888888 \
#   npm run e2e
#
#   # 一键本地全栈（需要 docker + backend/.venv）
#   npm run e2e:local
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT="$(cd "${WEB_DIR}/../.." && pwd)"

if [[ -z "${DDP_E2E_BASE_URL:-}" ]]; then
  echo "SKIPPED: 未提供 DDP_E2E_BASE_URL"
  echo "  说明：关键路径 E2E 需要可访问的后端与前端。"
  echo "  本地全栈：npm run e2e:local（需 docker + backend/.venv）"
  echo "  已有环境：DDP_E2E_BASE_URL=http://127.0.0.1:8088 \\"
  echo "            DDP_E2E_USER_ID=<项目成员 UUID> npm run e2e"
  exit 0
fi

if [[ -z "${DDP_E2E_USER_ID:-}" ]]; then
  echo "缺少 DDP_E2E_USER_ID（本地 Header 认证需要项目成员 UUID）" >&2
  exit 1
fi

cd "${WEB_DIR}"
exec ./node_modules/.bin/playwright test "$@"
