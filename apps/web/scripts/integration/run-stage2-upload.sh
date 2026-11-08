#!/usr/bin/env bash
# DDP-307：真实后端 + 真实 MinIO 的前端服务层联调。
#
# - 临时 MinIO 容器仅监听 127.0.0.1:9100，脚本退出时删除；
# - SQLite 使用 /tmp/ddp-it-307.db，仅本任务临时数据；
# - 不读取仓库 .env，后端在临时目录启动，环境变量全部显式注入；
# - 不访问 staging / xxx.xxx.xxx.xxx。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT="$(cd "${WEB_DIR}/../.." && pwd)"

DB_PATH="${DDP_IT_DB_PATH:-/tmp/ddp-it-307.db}"
MINIO_PORT="${DDP_IT_MINIO_PORT:-9100}"
API_PORT="${DDP_IT_API_PORT:-8010}"
MINIO_IMAGE="${DDP_IT_MINIO_IMAGE:-minio/minio:latest}"
CONTAINER="${DDP_IT_CONTAINER:-ddp-it-307-minio}"
SKIP_STORAGE="${DDP_IT_SKIP_STORAGE:-false}"
SETUP_ONLY="false"
SMOKE_ONLY="false"
RUN_MODE="full"
if [[ "${1:-}" == "--setup-only" ]]; then
  SETUP_ONLY="true"
  RUN_MODE="setup"
fi
if [[ "${1:-}" == "--smoke" ]]; then
  SMOKE_ONLY="true"
  SKIP_STORAGE="1"
  RUN_MODE="smoke"
fi
EVIDENCE_DIR="${ROOT}/docs/testing/evidence/2026-11-08-ddp-307"
TMP_DIR="$(mktemp -d /tmp/ddp-it-307.XXXXXX)"
PYTHON="${ROOT}/backend/.venv/bin/python"

export DDP_ENV="local"
export DDP_DATABASE_URL="sqlite+pysqlite:///${DB_PATH}"
export DDP_S3_ENDPOINT="http://127.0.0.1:${MINIO_PORT}"
export DDP_S3_BUCKET="ddp-raw"
export DDP_S3_ACCESS_KEY="minioadmin"
export DDP_S3_SECRET_KEY="minioadmin"
export DDP_LOCAL_HEADER_AUTH="true"
export DDP_RATE_LIMIT_ENABLED="false"
export DDP_UPLOAD_PART_SIZE_BYTES="5242880"

mkdir -p "${EVIDENCE_DIR}"
exec > >(tee "${EVIDENCE_DIR}/run-${RUN_MODE}.txt") 2>&1
rm -f "${DB_PATH}"

BACKEND_PID=""

cleanup() {
  local status=$?
  if [[ -n "${BACKEND_PID}" ]]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  echo "临时 MinIO 容器已删除；SQLite 保留在 ${DB_PATH}（仅本任务临时数据）。"
  exit "${status}"
}
trap cleanup EXIT

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令：$1" >&2
    exit 1
  fi
}

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

url = sys.argv[1]
timeout = float(sys.argv[2])
deadline = time.monotonic() + timeout
last_error: Exception | None = None
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001 - 轮询期间只记录最后错误
        last_error = exc
    time.sleep(0.5)
print(f"等待 {url} 超时：{last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

require_tool docker
require_tool "${PYTHON}"

if [[ "${SKIP_STORAGE}" != "1" && "${SKIP_STORAGE}" != "true" ]]; then
  if port_open "${MINIO_PORT}"; then
    echo "端口 ${MINIO_PORT} 已被占用；为避免影响现有服务，脚本终止。" >&2
    exit 1
  fi
fi
if port_open "${API_PORT}"; then
  echo "端口 ${API_PORT} 已被占用；为避免影响现有服务，脚本终止。" >&2
  exit 1
fi

if [[ "${SKIP_STORAGE}" == "1" || "${SKIP_STORAGE}" == "true" ]]; then
  echo "[1/6] 跳过 MinIO（仅诊断迁移/种子/后端启动）"
else
  echo "[1/6] 启动临时 MinIO（127.0.0.1:${MINIO_PORT}）"
  started=""
  for image in "${MINIO_IMAGE}" \
    "docker.1ms.run/minio/minio:latest" \
    "registry-1.docker.io/minio/minio:latest" \
    "quay.io/minio/minio:latest"; do
    if docker image inspect "${image}" >/dev/null 2>&1; then
      echo "使用本地镜像 ${image}"
    else
      echo "本地无 ${image}，尝试拉取"
      if ! docker pull "${image}"; then
        echo "拉取 ${image} 失败" >&2
        continue
      fi
    fi
    if docker run --rm -d \
      --name "${CONTAINER}" \
      -p "127.0.0.1:${MINIO_PORT}:9000" \
      -e MINIO_ROOT_USER=minioadmin \
      -e MINIO_ROOT_PASSWORD=minioadmin \
      "${image}" server /data >/dev/null 2>&1; then
      started="${image}"
      break
    fi
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  done
  if [[ -z "${started}" ]]; then
    echo "无法启动 MinIO：本地无镜像且 registry 拉取失败。" >&2
    echo "本地相关镜像：" >&2
    docker images --format '{{.Repository}}:{{.Tag}}' | head -50 >&2 || true
    exit 1
  fi
  wait_http "http://127.0.0.1:${MINIO_PORT}/minio/health/live" 60
fi

echo "[2/6] 在临时目录执行 Alembic 升级（不读取仓库 .env）"
"${PYTHON}" - "${ROOT}/backend/alembic.ini" "${TMP_DIR}/alembic.ini" "${ROOT}/backend/migrations" <<'PY'
from pathlib import Path
import sys

source, target, migrations = sys.argv[1:4]
text = Path(source).read_text(encoding="utf-8")
needle = "script_location = migrations"
if needle not in text:
    raise SystemExit("alembic.ini 缺少预期 script_location")
Path(target).write_text(
    text.replace(needle, f"script_location = {migrations}"),
    encoding="utf-8",
)
PY
(
  cd "${TMP_DIR}"
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_ENV="${DDP_ENV}" DDP_DATABASE_URL="${DDP_DATABASE_URL}" \
    "${PYTHON}" -m alembic -c "${TMP_DIR}/alembic.ini" upgrade head
)

echo "[3/6] 写入测试项目/成员并创建 ddp-raw 桶"
(
  cd "${TMP_DIR}"
  env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_DATABASE_URL="${DDP_DATABASE_URL}" \
    DDP_S3_ENDPOINT="${DDP_S3_ENDPOINT}" DDP_S3_BUCKET="${DDP_S3_BUCKET}" \
    DDP_S3_ACCESS_KEY="${DDP_S3_ACCESS_KEY}" DDP_S3_SECRET_KEY="${DDP_S3_SECRET_KEY}" \
    DDP_S3_REGION="us-east-1" \
    DDP_IT_SKIP_BUCKET="$([[ "${SKIP_STORAGE}" == "1" || "${SKIP_STORAGE}" == "true" ]] && echo 1 || echo 0)" \
    "${PYTHON}" "${SCRIPT_DIR}/seed_stage2.py"
)

echo "[4/6] 启动后端 127.0.0.1:${API_PORT}"
(
  cd "${TMP_DIR}"
  exec env -i PATH="${PATH}" HOME="${HOME}" PYTHONPATH="${ROOT}/backend" \
    DDP_ENV="${DDP_ENV}" DDP_DATABASE_URL="${DDP_DATABASE_URL}" \
    DDP_S3_ENDPOINT="${DDP_S3_ENDPOINT}" DDP_S3_BUCKET="${DDP_S3_BUCKET}" \
    DDP_S3_ACCESS_KEY="${DDP_S3_ACCESS_KEY}" DDP_S3_SECRET_KEY="${DDP_S3_SECRET_KEY}" \
    DDP_LOCAL_HEADER_AUTH="${DDP_LOCAL_HEADER_AUTH}" \
    DDP_RATE_LIMIT_ENABLED="${DDP_RATE_LIMIT_ENABLED}" \
    DDP_UPLOAD_PART_SIZE_BYTES="${DDP_UPLOAD_PART_SIZE_BYTES}" \
    "${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" \
    --log-level info
) >"${EVIDENCE_DIR}/backend.txt" 2>&1 &
BACKEND_PID=$!
wait_http "http://127.0.0.1:${API_PORT}/health" 60

if [[ "${SETUP_ONLY}" == "true" ]]; then
  echo "[5/6] setup-only：跳过集成测试（迁移、种子、后端健康检查已通过）"
  echo "[6/6] setup-only 完成"
  exit 0
fi

echo "[5/6] 运行真实后端集成测试"
if [[ "${SMOKE_ONLY}" == "true" ]]; then
  TEST_FILE="src/integration/stage2Contract.integration.test.ts"
  TEST_LOG="${EVIDENCE_DIR}/smoke.txt"
else
  TEST_FILE="src/integration/stage2Upload.integration.test.ts"
  TEST_LOG="${EVIDENCE_DIR}/integration.txt"
fi
(
  cd "${WEB_DIR}"
  DDP_IT_BASE_URL="http://127.0.0.1:${API_PORT}" \
  DDP_IT_PROJECT_ID="11111111-1111-7111-8111-111111111111" \
  DDP_IT_USER_ID="88888888-8888-7888-8888-888888888888" \
  DDP_DATABASE_URL="${DDP_DATABASE_URL}" \
  DDP_IT_BACKEND_DIR="${ROOT}/backend" \
  DDP_IT_PYTHON="${PYTHON}" \
  DDP_IT_ASSET_STATE_HELPER="${SCRIPT_DIR}/mark_asset_valid.py" \
  ./node_modules/.bin/vitest run \
    "${TEST_FILE}" \
    --reporter=verbose 2>&1 | tee "${TEST_LOG}"
)

echo "[6/6] 完成；原始输出见 ${TEST_LOG} 与 backend.txt"
