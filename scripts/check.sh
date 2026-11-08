#!/usr/bin/env bash
# 本地完整检查。依赖：backend/.venv、apps/web/node_modules。
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -x backend/.venv/bin/python ]]; then
  echo "缺少 backend/.venv，请先执行：make venv" >&2
  exit 1
fi

if [[ ! -d apps/web/node_modules ]]; then
  echo "缺少 apps/web/node_modules，请先执行：cd apps/web && npm install" >&2
  exit 1
fi

echo "[1/6] 后端 ruff"
(cd backend && .venv/bin/ruff check app tests migrations)

echo "[2/6] 后端 pytest"
(cd backend && .venv/bin/python -m pytest -q)

echo "[3/6] 前端类型检查 / ESLint / 单元测试"
(cd apps/web && npm run typecheck && npm run lint && npm test)

echo "[4/6] 前端构建"
(cd apps/web && npm run build)

echo "[5/6] Secret 扫描"
python3 scripts/check_secrets.py

echo "[6/6] Agent Bash 守卫测试"
backend/.venv/bin/python -m pytest scripts/tests -q

echo "全部检查通过"
