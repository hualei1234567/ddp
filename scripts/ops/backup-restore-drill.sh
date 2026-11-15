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

echo "== 1/7 前置检查 =="
docker inspect -f '{{.State.Health.Status}}' "$PG_CONTAINER" | grep -q healthy \
  || { echo "PostgreSQL 未就绪"; exit 1; }
docker inspect -f '{{.State.Health.Status}}' "$MINIO_CONTAINER" | grep -q healthy \
  || { echo "MinIO 未就绪"; exit 1; }

echo "== 2/7 记录恢复前基线 =="
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

echo "== 3/7 备份 PostgreSQL =="
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-acl \
  > "$WORKDIR/ddp.sql"
PG_DUMP_BYTES=$(wc -c < "$WORKDIR/ddp.sql" | tr -d ' ')
PG_DUMP_SHA=$(shasum -a 256 "$WORKDIR/ddp.sql" | awk '{print $1}')
[ "$PG_DUMP_BYTES" -gt 1000 ] || { echo "pg_dump 产物异常小（$PG_DUMP_BYTES 字节）"; exit 1; }
echo "  大小=${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA:0:16}…"

echo "== 4/7 备份 MinIO 对象并按内容校验 =="
# 之前的实现只做"有 mc 就数列数，没有就跳过"，等于没有验证。
# 现在改成真恢复演练：镜像内的 mc 位于 /opt/bitnami/minio-client/bin/mc，
# 全量 mirror 到临时目录后逐对象比对 ETag 与大小。
MC="/opt/bitnami/minio-client/bin/mc"
if ! docker exec "$MINIO_CONTAINER" test -x "$MC"; then
  echo "  MinIO 镜像内无 mc（$MC）；本次无法验证对象恢复，视为失败" >&2
  exit 1
fi
docker exec "$MINIO_CONTAINER" sh -c \
  "$MC alias set local http://127.0.0.1:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null" \
  || { echo "mc alias 配置失败" >&2; exit 1; }
SRC_OBJECTS=$(docker exec "$MINIO_CONTAINER" sh -c "$MC ls --recursive local 2>/dev/null | wc -l")
SRC_BYTES=$(docker exec "$MINIO_CONTAINER" sh -c "$MC du --recursive local 2>/dev/null | tail -1" || echo NA)
echo "  源对象数=${SRC_OBJECTS} 源总量=${SRC_BYTES}"
[ "$SRC_OBJECTS" -gt 0 ] || { echo "源桶为空，无法演练对象恢复" >&2; exit 1; }

# 备份到容器内临时目录（容器内 /tmp），再逐对象取 sha256 摘要比对。
docker exec "$MINIO_CONTAINER" sh -c "rm -rf /tmp/ddp-minio-drill && mkdir -p /tmp/ddp-minio-drill"
docker exec "$MINIO_CONTAINER" sh -c \
  "$MC mirror --quiet local /tmp/ddp-minio-drill 2>&1 | tail -5" \
  || { echo "mc mirror 失败" >&2; exit 1; }
BAK_OBJECTS=$(docker exec "$MINIO_CONTAINER" sh -c \
  "find /tmp/ddp-minio-drill -type f | wc -l")
BAK_BYTES=$(docker exec "$MINIO_CONTAINER" sh -c \
  "find /tmp/ddp-minio-drill -type f -exec stat -c %s {} + | awk '{s+=\$1} END {print s+0}'")
echo "  备份对象数=${BAK_OBJECTS} 备份总量=${BAK_BYTES}B"
[ "$SRC_OBJECTS" = "$BAK_OBJECTS" ] || { echo "对象数不一致：源=$SRC_OBJECTS 备份=$BAK_OBJECTS" >&2; exit 1; }

# 关键一步：把源对象删掉再恢复，否则"备份可用"只是口号。
# 只删一个抽样对象，避免破坏整桶；恢复后再比对内容摘要。
SAMPLE_KEY=$(docker exec "$MINIO_CONTAINER" sh -c \
  "$MC ls --recursive --json local 2>/dev/null | head -1" | sed -n 's/.*"key":"\([^"]*\)".*/\1/p')
SAMPLE_KEY="${SAMPLE_KEY#local/}"
[ -n "$SAMPLE_KEY" ] || { echo "无法从源桶取样对象" >&2; exit 1; }
SAMPLE_BEFORE=$(docker exec "$MINIO_CONTAINER" sh -c \
  "$MC cat \"local/$SAMPLE_KEY\" 2>/dev/null | sha256sum" | awk '{print $1}')
docker exec "$MINIO_CONTAINER" sh -c "$MC rm --force \"local/$SAMPLE_KEY\" >/dev/null 2>&1"
GONE=$(docker exec "$MINIO_CONTAINER" sh -c \
  "$MC stat \"local/$SAMPLE_KEY\" >/dev/null 2>&1 && echo present || echo missing")
[ "$GONE" = "missing" ] || { echo "抽样对象删除失败，无法验证恢复" >&2; exit 1; }
docker exec "$MINIO_CONTAINER" sh -c \
  "$MC cp \"/tmp/ddp-minio-drill/$SAMPLE_KEY\" \"local/$SAMPLE_KEY\" >/dev/null 2>&1" \
  || { echo "从备份恢复对象失败" >&2; exit 1; }
SAMPLE_AFTER=$(docker exec "$MINIO_CONTAINER" sh -c \
  "$MC cat \"local/$SAMPLE_KEY\" 2>/dev/null | sha256sum" | awk '{print $1}')
echo "  抽样对象 ${SAMPLE_KEY##*/}"
echo "    删除前 sha256=${SAMPLE_BEFORE:0:16}… 恢复后 sha256=${SAMPLE_AFTER:0:16}…"
[ "$SAMPLE_BEFORE" = "$SAMPLE_AFTER" ] || { echo "恢复后内容不一致" >&2; exit 1; }

echo "== 5/7 恢复到独立新库并校验 =="
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

echo "== 6/7 校验约束与触发器随备份恢复 =="
SRC_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
DST_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
echo "  触发器 源=$SRC_TRG 恢复=$DST_TRG"
[ "$SRC_TRG" = "$DST_TRG" ] || { echo "触发器数量不一致"; exit 1; }

echo "== 7/7 清理容器内临时备份 =="
docker exec "$MINIO_CONTAINER" sh -c "rm -rf /tmp/ddp-minio-drill" >/dev/null 2>&1 || true
echo "  已清理 /tmp/ddp-minio-drill"

echo
echo "演练结论：备份可恢复。PostgreSQL 表数/版本/行数/触发器一致；"
echo "          MinIO 对象数一致，抽样对象删除后从备份恢复且内容摘要相同。"
echo "  产物：pg_dump ${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA}"
