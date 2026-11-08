PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip

.PHONY: help venv infra-up infra-down api web test lint fmt check migrate

help:
	@echo "DDP 常用命令"
	@echo "  make venv        创建后端虚拟环境并安装依赖"
	@echo "  make infra-up    启动 PostgreSQL / Redis / MinIO"
	@echo "  make infra-down  停止本地基础设施"
	@echo "  make api         启动后端开发服务 (8000)"
	@echo "  make web         启动前端开发服务 (5173)"
	@echo "  make test        后端 pytest + 前端类型检查"
	@echo "  make lint        ruff + eslint"
	@echo "  make fmt         ruff format + prettier"
	@echo "  make check       完整本地检查"
	@echo "  make migrate     执行 Alembic 迁移"

venv:
	python3 -m venv backend/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements-dev.txt

infra-up:
	docker compose up -d postgres redis minio minio-init

infra-down:
	docker compose down

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

test:
	cd backend && .venv/bin/python -m pytest -q
	cd apps/web && npm run typecheck

lint:
	cd backend && .venv/bin/ruff check app tests migrations
	cd apps/web && npm run lint

fmt:
	cd backend && .venv/bin/ruff format app tests migrations
	cd apps/web && npx prettier --write 'src/**/*.{ts,tsx,css}'

check:
	bash scripts/check.sh

migrate:
	cd backend && .venv/bin/alembic upgrade head
