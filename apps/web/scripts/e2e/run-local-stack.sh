#!/usr/bin/env bash
# DDP-V21-N8-007 一键本地全栈 E2E：
#   临时 SQLite → Alembic 升级 → 种子（含真实 HTTP 状态推进）
#   → 临时后端（8010）→ Vite 预览（5174，代理到 8010）→ Playwright。
#
# 安全边界：
#   - 只使用本机回环地址与临时文件；
#   - 不读取仓库 .env（用 env -i 隔离），不写任何真实凭据；
#   - 退出时杀掉全部子进程并删除临时目录。
#
# 依赖：docker（MinIO 桶）、backend/.venv、apps/web/node_modules。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT="$(cd "${WEB_DIR}/../.." && pwd)"

PYTHON="${ROOT}/backend/.venv/bin/python"
API_PORT="${DDP_E2E_API_PORT:-8010}"
WEB_PORT="${DDP_E2E_WEB_PORT:-5174}"
MINIO_PORT="${DDP_E2E_MINIO_PORT:-9100}"
TMP_DIR="$(mktemp -d /tmp/ddp-e2e-n8-007.XXXXXX)"
DB_PATH="${TMP_DIR}/e2e.db"
BUCKET="ddp-e2e-n8-007"

BACKEND_PID=""
WEB_PID=""

cleanup() {
  local status=$?
  [[ -n "${WEB_PID}" ]] && kill "${WEB_PID}" >/dev/null 2>&1 || true
  [[ -n "${BACKEND_PID}" ]] && kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  wait >/dev/null 2>&1 || true
  rm -rf "${TMP_DIR}"
  exit "${status}"
}
trap cleanup EXIT

port_open() {
  "${PYTHON}" - "$1" <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
raise SystemExit(0)
PY
}

wait_http() {
  "${PYTHON}" - "$1" "$2" <<'PY'
import sys
import time
import urllib.request

url, timeout = sys.argv[1], float(sys.argv[2])
deadline = time.monotonic() + timeout
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception:  # noqa: BLE001 - 轮询期间忽略瞬时错误
        time.sleep(0.5)
raise SystemExit(1)
PY
}

for port in "${API_PORT}" "${WEB_PORT}"; do
  if port_open "${port}"; then
    echo "端口 ${port} 已被占用；为避免影响现有服务，脚本终止。" >&2
    exit 1
  fi
done

echo "[1/6] 临时 MinIO 桶（127.0.0.1:${MINIO_PORT}）"
"${PYTHON}" - "${MINIO_PORT}" "${BUCKET}" <<'PY'
import sys

import boto3
from botocore.exceptions import ClientError

port, bucket = sys.argv[1], sys.argv[2]
client = boto3.client(
    "s3",
    endpoint_url=f"http://127.0.0.1:{port}",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
)
try:
    client.head_bucket(Bucket=bucket)
except ClientError:
    client.create_bucket(Bucket=bucket)
PY

echo "[2/6] 临时 SQLite 迁移"
(
  cd "${ROOT}/backend"
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_ENV="local" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    "${PYTHON}" -m alembic upgrade head
)

echo "[3/6] 启动临时后端 127.0.0.1:${API_PORT}"
(
  cd "${TMP_DIR}"
  exec env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_ENV="local" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    DDP_LOCAL_HEADER_AUTH="true" \
    DDP_RATE_LIMIT_ENABLED="false" \
    DDP_S3_ENDPOINT="http://127.0.0.1:${MINIO_PORT}" \
    DDP_S3_BUCKET="${BUCKET}" \
    DDP_S3_ACCESS_KEY="minioadmin" \
    DDP_S3_SECRET_KEY="minioadmin" \
    DDP_S3_REGION="us-east-1" \
    DDP_UPLOAD_PART_SIZE_BYTES="5242880" \
    "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" \
    --log-level warning
) >"${TMP_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!
wait_http "http://127.0.0.1:${API_PORT}/health" 90 || {
  echo "后端启动失败，日志：" >&2
  cat "${TMP_DIR}/backend.log" >&2
  exit 1
}

echo "[4/6] 写入 E2E 种子（含真实 HTTP 状态推进）"
(
  cd "${TMP_DIR}"
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    DDP_E2E_API_BASE_URL="http://127.0.0.1:${API_PORT}" \
    "${PYTHON}" "${SCRIPT_DIR}/seed_e2e.py"
)

echo "[5/6] 构建并启动前端预览 127.0.0.1:${WEB_PORT}"
(
  cd "${WEB_DIR}"
  DDP_WEB_API_PROXY="http://127.0.0.1:${API_PORT}" \
    npm run build >"${TMP_DIR}/web-build.log" 2>&1 || {
      echo "前端构建失败，日志：" >&2
      cat "${TMP_DIR}/web-build.log" >&2
      exit 1
    }
)
(
  cd "${WEB_DIR}"
  exec env DDP_WEB_API_PROXY="http://127.0.0.1:${API_PORT}" \
    ./node_modules/.bin/vite preview --host 127.0.0.1 --port "${WEB_PORT}" \
    >"${TMP_DIR}/web.log" 2>&1
) &
WEB_PID=$!
wait_http "http://127.0.0.1:${WEB_PORT}/" 60 || {
  echo "前端预览启动失败，日志：" >&2
  cat "${TMP_DIR}/web.log" >&2
  exit 1
}

echo "[6/6] 运行 Playwright 关键路径"
cd "${WEB_DIR}"
DDP_E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
DDP_E2E_USER_ID="88888888-8888-7888-8888-888888888888" \
DDP_E2E_REVIEWER_ID="99999999-9999-7999-8999-999999999999" \
DDP_E2E_TASK_ID="22222222-2222-7222-8222-222222222222" \
DDP_E2E_IMPORT_ID="44444444-4444-7444-8444-444444444444" \
DDP_E2E_PROJECT_ID="11111111-1111-7111-8111-111111111111" \
  ./node_modules/.bin/playwright test "$@"
