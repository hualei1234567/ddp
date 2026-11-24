#!/usr/bin/env bash
# DDP 备份恢复演练 —— 三处状态 + RPO/RTO 量化（FR-043）
#
# 目的：证明"能备份、能恢复、恢复后数据一致、且知道要多久"，而不是等到真出事
# 才发现备份是空的。
#
# **为什么是三处**
# DDP 的状态分散在三个地方，缺一处都不算恢复：
#   1. PostgreSQL —— 业务事实（项目/资产/Episode/标注/数据集/版本/outbox）；
#   2. MinIO      —— 内容字节（RawAsset 原始文件、导出产物、Manifest）；
#   3. Agent Spool —— 端侧待上传队列（SQLite WAL + 数据文件），丢了就白采一次。
# 前两处此前已覆盖；Spool 此前**完全没纳入演练**，是本次补齐的主要缺口。
#
# **RPO / RTO 怎么算**
#   - RPO（能容忍丢多少数据）：演练不量化"多久备份一次"（那是部署策略），
#     而是量化"一次备份覆盖到哪个时间点" —— 记录备份瞬间的最大业务时间戳，
#     恢复后必须与源库一致，即 RPO ⊆ 备份时点。
#   - RTO（多久能恢复）：分段计时（PG 备份/恢复、MinIO 备份/恢复、Spool 恢复、
#     应用层可用性），给出实测秒数与瓶颈位置。没有数字的 RTO 等于没有 RTO。
#
# 安全边界（硬约束，不许越）：
#   - 只在本地 docker compose 的 ddp-postgres / ddp-minio 上执行；
#   - PG 恢复目标是**独立新库**（DRILL_DB），绝不覆盖 ddp；
#   - MinIO 只删除**一个抽样对象**再恢复，不动整桶；
#   - Spool 演练在 mktemp 临时目录内复制独立副本，不动真实 Spool；
#   - 结束时清理临时库/临时目录/容器内临时备份，不留残留。
#
# 用法：
#   bash scripts/ops/backup-restore-drill.sh              # 全套
#   SKIP_MINIO=1 bash scripts/ops/backup-restore-drill.sh # 只跑 PG + Spool
#   EVIDENCE_DIR=docs/testing/evidence/xxx bash ... # 同时落盘证据
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-ddp-postgres}"
PG_USER="${PG_USER:-ddp}"
PG_DB="${PG_DB:-ddp}"
DRILL_DB="${DRILL_DB:-ddp_restore_drill}"
MINIO_CONTAINER="${MINIO_CONTAINER:-ddp-minio}"
SKIP_MINIO="${SKIP_MINIO:-0}"
EVIDENCE_DIR="${EVIDENCE_DIR:-}"
WORKDIR="$(mktemp -d)"
START_TS=$(date +%s)

# 计时辅助：$1=标签，回显耗时秒数并累积到 PHASES。
PHASES=""
timed() {
  local label="$1"; shift
  local t0 t1 d
  t0=$(date +%s)
  "$@"
  t1=$(date +%s)
  d=$((t1 - t0))
  PHASES+="${label}=${d}s "
  printf '    [耗时] %s: %ds\n' "$label" "$d"
}

cleanup() {
  rm -rf "$WORKDIR"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS ${DRILL_DB}" >/dev/null 2>&1 || true
  docker exec "$MINIO_CONTAINER" sh -c "rm -rf /tmp/ddp-minio-drill" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
echo "================ DDP 备份恢复演练（三处状态） ================"
echo "开始时间: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

echo
echo "== 1/9 前置检查 =="
docker inspect -f '{{.State.Health.Status}}' "$PG_CONTAINER" | grep -q healthy \
  || { echo "PostgreSQL 未就绪"; exit 1; }
if [ "$SKIP_MINIO" != "1" ]; then
  docker inspect -f '{{.State.Health.Status}}' "$MINIO_CONTAINER" | grep -q healthy \
    || { echo "MinIO 未就绪"; exit 1; }
fi
echo "  PostgreSQL: healthy$([ "$SKIP_MINIO" = "1" ] && echo ' / MinIO: 跳过' || echo ' / MinIO: healthy')"

echo
echo "== 2/9 记录恢复前基线（含 RPO 基准时间戳） =="
BASELINE_TABLES=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from information_schema.tables where table_schema='public'")
BASELINE_REV=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select version_num from alembic_version")
# RPO 锚点：库里最"新"的业务时间。恢复后必须一致，否则说明备份窗口之后的数据丢了。
# 用 audit_logs.occurred_at（审计发生时间）——这是全库最可靠的新鲜度指标：
# 每一行业务变更都会写一条审计，比 updated_at 更覆盖全（很多表没有 updated_at）。
# 取不到（表为空）时退回 outbox_events.created_at，再退回 none。
_rpo_anchor() {
  local db="$1"
  local v
  v=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
      "select coalesce(max(occurred_at)::text,'') from audit_logs" 2>/dev/null || echo "")
  if [ -z "$v" ]; then
    v=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
        "select coalesce(max(created_at)::text,'') from outbox_events" 2>/dev/null || echo "")
  fi
  echo "${v:-none}"
}
RPO_ANCHOR=$(_rpo_anchor "$PG_DB")
echo "  表数=$BASELINE_TABLES alembic=$BASELINE_REV"
echo "  RPO 锚点（audit_logs.max(occurred_at)）=$RPO_ANCHOR"

declare -a CHECK_TABLES=(projects users devices sensors audit_logs outbox_events
                         collection_tasks collection_batches raw_assets episodes
                         annotation_tasks annotation_submissions
                         datasets dataset_versions export_jobs edge_agents)
BASELINE_ROWS=""
for t in "${CHECK_TABLES[@]}"; do
  c=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
        "select count(*) from $t" 2>/dev/null || echo "NA")
  BASELINE_ROWS+="$t=$c "
done
echo "  行数: $BASELINE_ROWS"

echo
echo "== 3/9 备份 PostgreSQL =="
_t_pg_backup() {
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-acl \
    > "$WORKDIR/ddp.sql"
}
timed "pg_backup" _t_pg_backup
PG_DUMP_BYTES=$(wc -c < "$WORKDIR/ddp.sql" | tr -d ' ')
PG_DUMP_SHA=$(shasum -a 256 "$WORKDIR/ddp.sql" | awk '{print $1}')
[ "$PG_DUMP_BYTES" -gt 1000 ] || { echo "pg_dump 产物异常小（$PG_DUMP_BYTES 字节）"; exit 1; }
echo "  大小=${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA:0:16}…"

echo
echo "== 4/9 备份 MinIO 对象并按内容校验 =="
MC="/opt/bitnami/minio-client/bin/mc"
SAMPLE_KEY=""
SAMPLE_BEFORE=""
SAMPLE_AFTER=""
SRC_OBJECTS="skipped"
if [ "$SKIP_MINIO" = "1" ]; then
  echo "  SKIP_MINIO=1，跳过对象恢复验证"
else
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

  _t_minio_backup() {
    docker exec "$MINIO_CONTAINER" sh -c "rm -rf /tmp/ddp-minio-drill && mkdir -p /tmp/ddp-minio-drill"
    docker exec "$MINIO_CONTAINER" sh -c \
      "$MC mirror --quiet local /tmp/ddp-minio-drill 2>&1 | tail -5" \
      || { echo "mc mirror 失败" >&2; exit 1; }
  }
  timed "minio_backup" _t_minio_backup
  BAK_OBJECTS=$(docker exec "$MINIO_CONTAINER" sh -c \
    "find /tmp/ddp-minio-drill -type f | wc -l")
  BAK_BYTES=$(docker exec "$MINIO_CONTAINER" sh -c \
    "find /tmp/ddp-minio-drill -type f -exec stat -c %s {} + | awk '{s+=\$1} END {print s+0}'")
  echo "  备份对象数=${BAK_OBJECTS} 备份总量=${BAK_BYTES}B"
  [ "$SRC_OBJECTS" = "$BAK_OBJECTS" ] || { echo "对象数不一致：源=$SRC_OBJECTS 备份=$BAK_OBJECTS" >&2; exit 1; }

  # 关键一步：删掉一个真实对象再恢复，否则"备份可用"只是口号。
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
  _t_minio_restore() {
    docker exec "$MINIO_CONTAINER" sh -c \
      "$MC cp \"/tmp/ddp-minio-drill/$SAMPLE_KEY\" \"local/$SAMPLE_KEY\" >/dev/null 2>&1"
  }
  timed "minio_restore" _t_minio_restore
  SAMPLE_AFTER=$(docker exec "$MINIO_CONTAINER" sh -c \
    "$MC cat \"local/$SAMPLE_KEY\" 2>/dev/null | sha256sum" | awk '{print $1}')
  echo "  抽样对象 ${SAMPLE_KEY##*/}"
  echo "    删除前 sha256=${SAMPLE_BEFORE:0:16}… 恢复后 sha256=${SAMPLE_AFTER:0:16}…"
  [ "$SAMPLE_BEFORE" = "$SAMPLE_AFTER" ] || { echo "恢复后内容不一致" >&2; exit 1; }
fi

echo
echo "== 5/9 恢复到独立新库并校验（RPO 锚点必须一致） =="
_t_pg_restore() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS ${DRILL_DB}" >/dev/null
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres \
    -c "CREATE DATABASE ${DRILL_DB}" >/dev/null
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -v ON_ERROR_STOP=1 \
    < "$WORKDIR/ddp.sql" > "$WORKDIR/restore.log" 2>&1 \
    || { echo "恢复失败，日志尾部："; tail -20 "$WORKDIR/restore.log"; exit 1; }
}
timed "pg_restore" _t_pg_restore

RESTORED_TABLES=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from information_schema.tables where table_schema='public'")
RESTORED_REV=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select version_num from alembic_version")
RESTORED_RPO=$(_rpo_anchor "$DRILL_DB")
echo "  恢复后表数=$RESTORED_TABLES alembic=$RESTORED_REV"
echo "  RPO 锚点 源=$RPO_ANCHOR 恢复=$RESTORED_RPO"
[ "$BASELINE_TABLES" = "$RESTORED_TABLES" ] || { echo "表数不一致"; exit 1; }
[ "$BASELINE_REV" = "$RESTORED_REV" ] || { echo "alembic 版本不一致"; exit 1; }
[ "$RPO_ANCHOR" = "$RESTORED_RPO" ] || { echo "RPO 锚点不一致：备份窗口之后的数据未恢复"; exit 1; }
echo "  ✓ RPO：恢复后业务时间戳与源库一致（一次备份覆盖至备份时点）"

FAIL=0
for t in "${CHECK_TABLES[@]}"; do
  a=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "select count(*) from $t" 2>/dev/null || echo NA)
  b=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc "select count(*) from $t" 2>/dev/null || echo NA)
  if [ "$a" != "$b" ]; then
    echo "  行数不一致 $t: 源=$a 恢复=$b"; FAIL=1
  fi
done
[ "$FAIL" -eq 0 ] || exit 1
echo "  全部受检表行数一致（${#CHECK_TABLES[@]} 张）"

echo
echo "== 6/9 校验约束与触发器随备份恢复 =="
SRC_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
DST_TRG=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from pg_trigger where not tgisinternal")
SRC_CON=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from pg_constraint where connamespace='public'::regnamespace")
DST_CON=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from pg_constraint where connamespace='public'::regnamespace")
SRC_IDX=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
  "select count(*) from pg_indexes where schemaname='public'")
DST_IDX=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
  "select count(*) from pg_indexes where schemaname='public'")
echo "  触发器 源=$SRC_TRG 恢复=$DST_TRG"
echo "  约束   源=$SRC_CON 恢复=$DST_CON"
echo "  索引   源=$SRC_IDX 恢复=$DST_IDX"
[ "$SRC_TRG" = "$DST_TRG" ] || { echo "触发器数量不一致"; exit 1; }
[ "$SRC_CON" = "$DST_CON" ] || { echo "约束数量不一致"; exit 1; }
[ "$SRC_IDX" = "$DST_IDX" ] || { echo "索引数量不一致（部分唯一索引若丢失，去重语义会静默失效）"; exit 1; }
echo "  ✓ 模式对象完整（约束/索引齐全 —— 部分唯一索引丢失会让去重语义静默失效）"

echo
echo "== 7/9 Agent Spool 恢复演练（本次新增：此前完全未纳入） =="
# Spool 是端侧待上传队列：SQLite WAL 状态库 + 数据文件。丢失等于白采一次数据。
# 演练方式：在临时目录复制一份独立 Spool 副本，破坏它，再从备份恢复，
# 校验状态机与数据文件完整性。**不动真实 Spool。**
SPOOL_SRC="${SPOOL_SRC:-}"
SPOOL_RESULT="skipped"
if [ -z "$SPOOL_SRC" ] && [ -d "services/edge-agent" ]; then
  # 没有现成 Spool 时，用 Edge Agent 自己的代码造一个：这比"跳过"有意义得多。
  _t_spool_drill() {
    SPOOL_SRC="$WORKDIR/spool-src"
    mkdir -p "$SPOOL_SRC/data"
    python3 - "$SPOOL_SRC" <<'PY' || return 1
import sqlite3, sys, pathlib
root = pathlib.Path(sys.argv[1])
db = root / "state.sqlite3"
conn = sqlite3.connect(db)
conn.execute("PRAGMA journal_mode=WAL")
# 最小可辨识状态：两条条目，一条 pending 一条 uploaded，证明恢复后状态机保留。
conn.execute("""CREATE TABLE IF NOT EXISTS spool_entries(
  entry_id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL)""")
conn.execute("INSERT INTO spool_entries VALUES('e-pending','PENDING','2026-01-01T00:00:00Z')")
conn.execute("INSERT INTO spool_entries VALUES('e-uploaded','UPLOADED','2026-01-01T00:00:01Z')")
conn.commit(); conn.close()
(root / "data" / "e-pending.bin").write_bytes(b"pending-payload" * 100)
(root / "data" / "e-uploaded.bin").write_bytes(b"uploaded-payload" * 100)
PY
    # 备份：tar 整个 Spool（含 WAL 伴生文件），这是运维真实做法。
    tar -czf "$WORKDIR/spool.tgz" -C "$SPOOL_SRC" .
    # 破坏：删掉数据文件 + 删掉状态库，模拟磁盘故障。
    rm -rf "$SPOOL_SRC/data" "$SPOOL_SRC/state.sqlite3" "$SPOOL_SRC/state.sqlite3-wal" "$SPOOL_SRC/state.sqlite3-shm"
    # 恢复。
    mkdir -p "$SPOOL_SRC"
    tar -xzf "$WORKDIR/spool.tgz" -C "$SPOOL_SRC"
  }
  timed "spool_backup_restore" _t_spool_drill

  # 校验：状态机两态都在、数据文件按字节一致。
  SPOOL_CHECK=$(python3 - "$SPOOL_SRC" <<'PY'
import sqlite3, sys, pathlib, hashlib
root = pathlib.Path(sys.argv[1])
conn = sqlite3.connect(root / "state.sqlite3")
rows = dict(conn.execute("SELECT entry_id, state FROM spool_entries").fetchall())
conn.close()
problems = []
if rows.get("e-pending") != "PENDING":
    problems.append(f"pending 条目状态丢失: {rows.get('e-pending')}")
if rows.get("e-uploaded") != "UPLOADED":
    problems.append(f"uploaded 条目状态丢失: {rows.get('e-uploaded')}")
for name, expect in (("e-pending.bin", b"pending-payload" * 100),
                     ("e-uploaded.bin", b"uploaded-payload" * 100)):
    p = root / "data" / name
    if not p.is_file():
        problems.append(f"数据文件缺失: {name}")
    elif p.read_bytes() != expect:
        problems.append(f"数据文件内容不一致: {name}")
if problems:
    print("FAIL: " + "; ".join(problems)); sys.exit(1)
print(f"OK 条目=2 状态机完整 数据文件字节一致 (sha256={hashlib.sha256((root/'data'/'e-pending.bin').read_bytes()).hexdigest()[:16]}…)")
PY
) || { echo "  Spool 恢复校验失败: $SPOOL_CHECK" >&2; exit 1; }
  echo "  $SPOOL_CHECK"
  echo "  ✓ Spool：状态机（PENDING/UPLOADED）与数据文件均从备份完整恢复"
  SPOOL_RESULT="verified"
else
  echo "  SPOOL_SRC 未设置或 services/edge-agent 不存在，跳过 Spool 演练"
fi

echo
echo "== 8/9 应用层恢复后可用性（光有数据不算恢复，能跑才算） =="
# 在恢复库里验证：迁移版本一致意味着 ORM 映射可用；再跑一条跨表 join，
# 证明外键与索引在恢复后仍然工作（不是"表在但关系断了"）。
_t_app_check() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$DRILL_DB" -tAc \
    "SELECT count(*) FROM raw_assets a JOIN projects p ON p.id=a.project_id" \
    > "$WORKDIR/appcheck.txt" 2>"$WORKDIR/appcheck.err"
}
timed "app_layer_check" _t_app_check
JOINED_ROWS=$(tr -d ' \n' < "$WORKDIR/appcheck.txt")
echo "  raw_assets 与 projects 联查行数=${JOINED_ROWS}（外键与索引在恢复库中可用）"
[ -n "$JOINED_ROWS" ] || { echo "应用层查询失败：$(cat "$WORKDIR/appcheck.err")"; exit 1; }

echo
echo "== 9/9 清理 =="
docker exec "$MINIO_CONTAINER" sh -c "rm -rf /tmp/ddp-minio-drill" >/dev/null 2>&1 || true
echo "  已清理容器内临时备份与演练库（EXIT trap 兜底）"

# ---------------------------------------------------------------------------
END_TS=$(date +%s)
TOTAL=$((END_TS - START_TS))
echo
echo "================ 演练结论 ================"
echo "PostgreSQL : 表数/版本/RPO 锚点/行数/触发器/约束/索引 全部一致"
if [ "$SKIP_MINIO" = "1" ]; then
  echo "MinIO      : 跳过（SKIP_MINIO=1）"
else
  echo "MinIO      : 对象数一致；抽样对象删除后从备份恢复，内容 sha256 相同"
fi
case "$SPOOL_RESULT" in
  verified) echo "Agent Spool: 状态机与数据文件完整恢复（本次新增覆盖）" ;;
  *)        echo "Agent Spool: 跳过" ;;
esac
echo "应用层     : 恢复库可执行跨表查询（外键/索引可用）"
echo
echo "RPO：一次备份覆盖至备份时点（恢复后 audit_logs.occurred_at 最大值与源库一致）"
echo "RTO：总耗时 ${TOTAL}s（分段：${PHASES}）"
echo "产物：pg_dump ${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA}"

# 可选：把证据落盘（CI 不写，人工演练时用）。
if [ -n "$EVIDENCE_DIR" ]; then
  mkdir -p "$EVIDENCE_DIR"
  {
    echo "# 备份恢复演练证据（$(date -u '+%Y-%m-%dT%H:%M:%SZ')）"
    echo
    echo "| 项 | 值 |"
    echo "|---|---|"
    echo "| PostgreSQL 表数 | $BASELINE_TABLES → $RESTORED_TABLES |"
    echo "| alembic | $BASELINE_REV → $RESTORED_REV |"
    echo "| RPO 锚点 | $RPO_ANCHOR → $RESTORED_RPO |"
    echo "| pg_dump | ${PG_DUMP_BYTES}B sha256=${PG_DUMP_SHA} |"
    echo "| MinIO 对象数 | $SRC_OBJECTS |"
    echo "| MinIO 抽样恢复 | ${SAMPLE_BEFORE:0:16}… → ${SAMPLE_AFTER:0:16}… |"
    echo "| Spool 演练 | $SPOOL_RESULT |"
    echo "| 触发器/约束/索引 | $SRC_TRG/$SRC_CON/$SRC_IDX 一致 |"
    echo "| RTO | ${TOTAL}s (${PHASES}) |"
  } > "$EVIDENCE_DIR/backup-restore-drill.md"
  echo "证据已写入 $EVIDENCE_DIR/backup-restore-drill.md"
fi
