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

echo "[1/7] 后端 ruff check + format"
(cd backend && .venv/bin/ruff check app tests migrations && .venv/bin/ruff format --check app tests migrations)

echo "[2/7] 后端 pytest"
(cd backend && .venv/bin/python -m pytest -q)

echo "[3/7] 前端类型检查 / ESLint / 单元测试"
(cd apps/web && npm run typecheck && npm run lint && npm test)

echo "[4/7] 前端构建"
(cd apps/web && npm run build)

echo "[5/7] Secret 扫描"
python3 scripts/check_secrets.py

echo "[6/7] Agent Bash 守卫测试"
backend/.venv/bin/python -m pytest scripts/tests -q

echo "[7/7] outbox 事件归类自检（投递类必须有消费者与测试）"
python3 scripts/check_outbox_events.py

echo "全部检查通过"
