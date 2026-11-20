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
# 依赖：调用方已启动的 MinIO、backend/.venv（或 DDP_E2E_PYTHON）、
# apps/web/node_modules。本脚本只创建临时桶，不负责启动或停止 MinIO。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT="$(cd "${WEB_DIR}/../.." && pwd)"

PYTHON="${DDP_E2E_PYTHON:-${ROOT}/backend/.venv/bin/python}"
API_PORT="${DDP_E2E_API_PORT:-8010}"
WEB_PORT="${DDP_E2E_WEB_PORT:-5174}"
MINIO_PORT="${DDP_E2E_MINIO_PORT:-9100}"
TMP_DIR="$(mktemp -d /tmp/ddp-e2e-n8-007.XXXXXX)"
DB_PATH="${TMP_DIR}/e2e.db"
BUCKET="ddp-e2e-n8-007"

BACKEND_PID=""
WORKER_PID=""
WEB_PID=""

cleanup() {
  local status=$?
  if [[ "${status}" -ne 0 && -f "${TMP_DIR}/worker.log" ]]; then
    echo "上传校验 Worker 日志：" >&2
    cat "${TMP_DIR}/worker.log" >&2
  fi
  [[ -n "${WEB_PID}" ]] && kill "${WEB_PID}" >/dev/null 2>&1 || true
  [[ -n "${WORKER_PID}" ]] && kill "${WORKER_PID}" >/dev/null 2>&1 || true
  [[ -n "${BACKEND_PID}" ]] && kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  wait >/dev/null 2>&1 || true
  rm -rf "${TMP_DIR}"
  exit "${status}"
}
trap cleanup EXIT

if [[ ! -x "${PYTHON}" ]]; then
  echo "Python 不可执行：${PYTHON}；请创建 backend/.venv 或设置 DDP_E2E_PYTHON。" >&2
  exit 1
fi

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

echo "[1/6] 在调用方已启动的 MinIO 创建临时桶（127.0.0.1:${MINIO_PORT}）"
if ! port_open "${MINIO_PORT}"; then
  echo "MinIO 端口 ${MINIO_PORT} 不可用；本脚本不负责启动 MinIO，请先启动仓库 docker compose 的 minio 服务。" >&2
  exit 1
fi
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
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend:${ROOT}/data-platform" \
    DDP_ENV="local" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    "${PYTHON}" -m alembic upgrade head
)

echo "[3/6] 启动临时后端 127.0.0.1:${API_PORT}"
(
  cd "${TMP_DIR}"
  exec env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend:${ROOT}/data-platform" \
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
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend:${ROOT}/data-platform" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    DDP_E2E_API_BASE_URL="http://127.0.0.1:${API_PORT}" \
    "${PYTHON}" "${SCRIPT_DIR}/seed_e2e.py"
)

# 用仓库已固定版本的官方 MCAP SDK生成最小完整 fixture。它包含 header、
# schema、channel、message、footer/summary 和首尾 magic，Worker 必须能够真实解析。
MCAP_PATH="${TMP_DIR}/e2e-episode.mcap"
env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend:${ROOT}/data-platform" \
  "${PYTHON}" - "${MCAP_PATH}" <<'PY'
import sys
from pathlib import Path

from mcap.writer import CompressionType, Writer

path = Path(sys.argv[1])
with path.open("wb") as stream:
    writer = Writer(stream, compression=CompressionType.NONE)
    writer.start(profile="robot/teleop", library="ddp-e2e")
    schema_id = writer.register_schema(
        name="ddp.e2e.Frame", encoding="jsonschema", data=b'{"type":"object"}'
    )
    channel_id = writer.register_channel(
        topic="/e2e/frame", message_encoding="json", schema_id=schema_id
    )
    writer.add_message(
        channel_id=channel_id, log_time=1_000_000_000,
        publish_time=1_000_000_000, data=b'{"frame":1}'
    )
    writer.finish()
PY

# 完成上传只登记待校验资产；独立 outbox Worker 才会读取对象、校验签名并
# 把会话推进到终态。与生产边界一致，不在 HTTP 线程处理文件。
# 放在种子之后启动，避免 Worker 轮询与种子写入争用 SQLite 写锁。
(
  cd "${TMP_DIR}"
  exec env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend:${ROOT}/data-platform" \
    DDP_ENV="local" \
    DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
    DDP_S3_ENDPOINT="http://127.0.0.1:${MINIO_PORT}" \
    DDP_S3_BUCKET="${BUCKET}" \
    DDP_S3_ACCESS_KEY="minioadmin" \
    DDP_S3_SECRET_KEY="minioadmin" \
    DDP_S3_REGION="us-east-1" \
    "${PYTHON}" -m app.workers.outbox
) >"${TMP_DIR}/worker.log" 2>&1 &
WORKER_PID=$!

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
DDP_E2E_MCAP_PATH="${MCAP_PATH}" \
  ./node_modules/.bin/playwright test "$@"
