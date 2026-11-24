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

echo "[1/10] 后端 ruff check + format"
(cd backend && .venv/bin/ruff check app tests migrations && .venv/bin/ruff format --check app tests migrations)

echo "[2/10] 后端 pytest"
(cd backend && .venv/bin/python -m pytest -q)

echo "[3/10] 前端类型检查 / ESLint / 单元测试"
(cd apps/web && npm run typecheck && npm run lint && npm test)

echo "[4/10] 前端构建"
(cd apps/web && npm run build)

echo "[5/10] Secret 扫描"
python3 scripts/check_secrets.py

echo "[6/10] Agent Bash 守卫测试"
PYTHONPATH="backend:data-platform${PYTHONPATH:+:${PYTHONPATH}}" \
  backend/.venv/bin/python -m pytest scripts/tests -q

echo "[7/10] outbox 事件归类自检（投递类必须有消费者与测试）"
python3 scripts/check_outbox_events.py

echo "[8/10] 契约一致性（路径/字段/错误码可达；只对基线之外的差异失败）"
# --strict 用 packages/contracts/parity-baseline.json 里的「已核实无害」清单，
# 只对新增差异红灯。基线里每一项都写了为何无害，见该文件的 _why。
backend/.venv/bin/python scripts/check_contract_parity.py --strict

echo "[9/10] Canonical JSON Schema 与字段声明一致（生成产物不得手改）"
python3 scripts/export_canonical_schemas.py --check

echo "[10/10] 记忆库校验（格式/索引/check_ref 有效性）"
# 记忆库没有校验必然腐烂：卡片格式写错、check_ref 指向已删除的测试、
# 索引与卡片不一致——任一发生，"记忆"就变成不可信文档（比没有更糟）。
python3 scripts/check_memory.py

echo "全部检查通过"
