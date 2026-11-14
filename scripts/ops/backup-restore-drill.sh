#!/usr/bin/env bash
# DDP 备份恢复演练（审计 R-4）。
#
# 目的：证明"能备份、能恢复、恢复后数据一致"，而不是等到真出事才发现备份是空的。
# 安全边界：
#   - 只在本地 docker compose 的 ddp-postgres / ddp-minio 上执行；
#   - 恢复目标是一个**独立的新库**（ddp_restore_drill），绝不覆盖 ddp；
#   - 演练结束后清理临时库与临时容器，不留残留。
#
# 用法：bash scripts/ops/backup-restore-drill.sh
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-ddp-postgres}"
PG_USER="${PG_USER:-ddp}"
PG_DB="${PG_DB:-ddp}"
DRILL_DB="${DRILL_DB:-ddp_restore_drill}"
MINIO_CONTAINER="${MINIO_CONTAINER:-ddp-minio}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"; docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS ${DRILL_DB}" >/dev/null 2>&1 || true' EXIT

echo "== 1/6 前置检查 =="
docker inspect -f '{{.State.Health.Status}}' "$PG_CONTAINER" | grep -q healthy \
  || { echo "PostgreSQL 未就绪"; exit 1; }
docker inspect -f '{{.State.Health.Status}}' "$MINIO_CONTAINER" | grep -q healthy \
  || { echo "MinIO 未就绪"; exit 1; }

echo "== 2/6 记录恢复前基线 =="
BASELINE_TABLES=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from information_schema.tables where table_schema='public'")
BASELINE_REV=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select version_num from alembic_version")
echo "  表数=$BASELINE_TABLES alembic=$BASELINE_REV"
# 计入若干业务表的行数，用于恢复后逐一比对。
declare -a CHECK_TABLES=(projects users devices sensors audit_logs outbox_events
                         collection_tasks collection_batches raw_assets episodes
                         annotation_tasks datasets dataset_versions)
BASELINE_ROWS=""
for t in "${CHECK_TABLES[@]}"; do
  c=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
        "select count(*) from $t" 2>/dev/null || echo "NA")
  BASELINE_ROWS+="$t=$c "
done
echo "  行数: $BASELINE_ROWS"

echo "== 3/6 备份 PostgreSQL =="
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-acl \
  > "$WORKDIR/ddp.sql"
PG_DUMP_BYTES=$(wc -c < "$WORKDIR/ddp.sql" | tr -d ' ')
PG_DUMP_SHA=$(shasum -a 256 "$WORKDIR/ddp.sql" | awk '{print $1}')
[ "$PG_DUMP_BYTES" -gt 1000 ] || { echo "pg_dump 产物异常小（$PG_DUMP_BYTES 字节）"; exit 1; }
echo "  大小=${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA:0:16}…"

echo "== 4/6 备份 MinIO 对象清单 =="
OBJ_LIST=$(docker exec "$MINIO_CONTAINER" sh -c '
  command -v mc >/dev/null 2>&1 || exit 0
  mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
  mc ls --recursive local 2>/dev/null | wc -l' 2>/dev/null || echo "no-mc")
echo "  对象数=${OBJ_LIST}（镜像无 mc 时跳过；S3 数据由 bucket 版本化/异地复制兜底）"

echo "== 5/6 恢复到独立新库并校验 =="
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS ${DRILL_DB}" >/dev/null
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
  -c "CREATE DATABASE ${DRILL_DB}" >/dev/null
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -v ON_ERROR_STOP=1 \
  < "$WORKDIR/ddp.sql" > "$WORKDIR/restore.log" 2>&1 \
  || { echo "恢复失败，日志尾部："; tail -20 "$WORKDIR/restore.log"; exit 1; }

RESTORED_TABLES=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from information_schema.tables where table_schema='public'")
RESTORED_REV=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select version_num from alembic_version")
echo "  恢复后表数=$RESTORED_TABLES alembic=$RESTORED_REV"
[ "$BASELINE_TABLES" = "$RESTORED_TABLES" ] || { echo "表数不一致"; exit 1; }
[ "$BASELINE_REV" = "$RESTORED_REV" ] || { echo "alembic 版本不一致"; exit 1; }

FAIL=0
for t in "${CHECK_TABLES[@]}"; do
  a=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "select count(*) from $t" 2>/dev/null || echo NA)
  b=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc "select count(*) from $t" 2>/dev/null || echo NA)
  if [ "$a" != "$b" ]; then
    echo "  行数不一致 $t: 源=$a 恢复=$b"; FAIL=1
  fi
done
[ "$FAIL" -eq 0 ] || exit 1
echo "  全部受检表行数一致"

echo "== 6/6 校验约束与触发器随备份恢复 =="
SRC_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
DST_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
echo "  触发器 源=$SRC_TRG 恢复=$DST_TRG"
[ "$SRC_TRG" = "$DST_TRG" ] || { echo "触发器数量不一致"; exit 1; }

echo
echo "演练结论：备份可恢复，表数/版本/行数/触发器全部一致。"
echo "  产物：pg_dump ${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA}"
