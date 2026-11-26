#!/usr/bin/env bash
# 本地 Semgrep SAST（审计 R4 / V52-042）。
#
# 覆盖注入（SQL 拼接、shell=True）与 SSRF 两类此前"全靠人眼"的问题：
#   - 官方规则集：p/python + p/security-audit（通过 --config 引用，不内联）
#   - 自定义规则：.semgrep.yml（本项目真实风险面，3 条）
#
# 与 CI 的 security job「Semgrep SAST（阻断）」用同一套命令：CI 只负责
# `pip install semgrep`，扫描命令以本脚本为单一来源，避免两处漂移。
#
# 用法：
#   bash scripts/check_sast.sh
#   SEMGREP_BIN=/path/to/semgrep bash scripts/check_sast.sh
#
# 发现阻断级问题时退出码为 1；未安装 semgrep 时退出码为 2（不静默跳过）。
set -euo pipefail

cd "$(dirname "$0")/.."

# 引擎版本固定：semgrep 版本变化会改变规则匹配行为，升级需本地复核基线。
# 官方规则集来自 registry，其内容随上游更新，无法在此固定——见到新告警先
# 判断真假，再决定改代码还是加 `# nosemgrep: <rule-id>` 并写明理由。
SEMGREP_VERSION="${SEMGREP_VERSION:-1.187.0}"

SEMGREP="${SEMGREP_BIN:-}"
if [[ -z "$SEMGREP" ]]; then
  if command -v semgrep >/dev/null 2>&1; then
    SEMGREP="$(command -v semgrep)"
  else
    cat >&2 <<EOF
[SAST] 未找到 semgrep。请先安装（不引入 Docker，也不写入 requirements）：

  python -m pip install "semgrep==${SEMGREP_VERSION}"

或指定已有可执行文件：

  SEMGREP_BIN=/path/to/semgrep bash scripts/check_sast.sh
EOF
    exit 2
  fi
fi

# 只扫真实代码面：不扫 tests / migrations（大量构造非法输入的用例与 DDL 文本
# 会淹没信号）。范围与 .semgrepignore 的兜底口径一致。
TARGETS=(backend/app scripts)

# 排除的官方规则（规则过宽 → 收敛规则，而不是逐条静默）：
#   avoid-sqlalchemy-text 对本仓库"text()"一律报错，但 DDP 的 text() 只用于
#   SQLAlchemy Core 无法表达的 DDL/运维语句（CREATE TRIGGER / CREATE DATABASE /
#   SET LOCAL / 部分索引），且值一律走 bind 参数。注入风险由自定义规则
#   ddp-no-raw-sql-fstring（只盯拼接）覆盖，保留该官方规则只会让每一条合法
#   原生 SQL 都需要 nosemgrep。若将来 text() 开始承接请求输入，本规则应恢复。
EXCLUDE_RULES=(
  "python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text"
)

EXCLUDE_ARGS=()
for rule in "${EXCLUDE_RULES[@]}"; do
  EXCLUDE_ARGS+=("--exclude-rule" "$rule")
done

echo "[SAST] semgrep $("$SEMGREP" --version) 扫描：p/python + p/security-audit + .semgrep.yml"
echo "[SAST] 目标：${TARGETS[*]}（tests/migrations 不在范围内）"

set +e
"$SEMGREP" scan \
  --config p/python \
  --config p/security-audit \
  --config .semgrep.yml \
  "${EXCLUDE_ARGS[@]}" \
  --error \
  --metrics=off \
  --quiet \
  "${TARGETS[@]}"
SAST_EXIT=$?
set -e

if [[ $SAST_EXIT -ne 0 ]]; then
  cat >&2 <<'EOF'

[SAST] 失败：Semgrep 发现了阻断级问题（--error 模式，退出码非 0）。

处理顺序（不要为了变绿直接 nosemgrep）：
  1. 逐条读告警和规则消息，确认真实可达路径（请求参数 / 配置 / 外部数据）；
  2. 真问题：按规则消息修复（bind 参数 / 参数列表调用 / 出站前
     ensure_safe_adapter_url 校验），并在复盘里登记；
  3. 误报：在**告警起始行**或上一行加 `# nosemgrep: <rule-id>`，同一行后
     写清"为什么这里不可控/已有等价校验"，禁止无理由忽略；
  4. 规则本身过宽：收敛 .semgrep.yml 或 scripts/check_sast.sh 里的排除项，
     并在提交信息里写明理由。
EOF
  exit 1
fi

echo "[SAST] 通过：未发现阻断级问题"
