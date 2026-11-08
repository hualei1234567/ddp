#!/usr/bin/env bash
# 阶段 2 契约冻结基线的生成与兼容性检查。
#
# 用法：
#   bash packages/contracts/scripts/check-stage2-freeze.sh --freeze
#   bash packages/contracts/scripts/check-stage2-freeze.sh --check
#
# --freeze：对 openapi.yaml 执行 Redocly lint，随后 bundle 为自包含基线，
#           并记录 SHA256。默认拒绝覆盖已有基线，使用 --force 才覆盖。
# --check ：校验基线 SHA256 未变，执行 Redocly lint，再用 oasdiff 比较
#           基线 → 当前契约的破坏性变更。
#
# 依赖：npx（@redocly/cli@1）；oasdiff（仅 --check 需要）。
# 本脚本不会自动提交或推送；基线文件与报告均留在工作区供审阅。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CONTRACT_DIR="${ROOT}/packages/contracts"
CURRENT="${CONTRACT_DIR}/openapi.yaml"
BASELINE_DIR="${CONTRACT_DIR}/baselines"
BASELINE="${BASELINE_DIR}/openapi.stage2.v1.0.0.bundle.yaml"
SHA_FILE="${BASELINE_DIR}/openapi.stage2.v1.0.0.sha256"
REPORT_DIR="${CONTRACT_DIR}/reports"

MODE="check"
FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --freeze) MODE="freeze" ;;
    --check) MODE="check" ;;
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "未知参数：${arg}" >&2
      exit 2
      ;;
  esac
done

mkdir -p "${BASELINE_DIR}" "${REPORT_DIR}"
REPORT="${REPORT_DIR}/stage2-freeze-$(date -u +%Y%m%dT%H%M%SZ).txt"
exec > >(tee "${REPORT}") 2>&1

echo "契约冻结脚本"
echo "  模式：${MODE}"
echo "  当前：${CURRENT}"
echo "  基线：${BASELINE}"
echo "  报告：${REPORT}"
echo "  时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

run_redocly() {
  if command -v redocly >/dev/null 2>&1; then
    command redocly "$@"
  elif command -v npx >/dev/null 2>&1; then
    npx --yes @redocly/cli@1 "$@"
  else
    echo "未找到 redocly 或 npx；请安装 @redocly/cli@1。" >&2
    return 1
  fi
}

run_lint() {
  echo "== Redocly lint =="
  run_redocly lint "${CURRENT}" --config "${CONTRACT_DIR}/redocly.yaml"
  echo
}

case "${MODE}" in
  freeze)
    if [[ -f "${BASELINE}" && "${FORCE}" -ne 1 ]]; then
      echo "基线已存在：${BASELINE}" >&2
      echo "如需重新冻结，请显式传入 --force。" >&2
      exit 1
    fi
    run_lint
    echo "== 生成自包含基线（Redocly bundle）=="
    run_redocly bundle "${CURRENT}" -o "${BASELINE}"
    sha256sum "${BASELINE}" | awk '{print $1}' > "${SHA_FILE}"
    echo "基线已写入：${BASELINE}"
    echo "SHA256：$(cat "${SHA_FILE}")"
    ;;
  check)
    if [[ ! -f "${BASELINE}" ]]; then
      echo "基线不存在：${BASELINE}" >&2
      echo "请先执行：bash packages/contracts/scripts/check-stage2-freeze.sh --freeze" >&2
      exit 1
    fi
    if [[ ! -f "${SHA_FILE}" ]]; then
      echo "基线 SHA256 文件不存在：${SHA_FILE}" >&2
      exit 1
    fi
    expected="$(cat "${SHA_FILE}")"
    actual="$(sha256sum "${BASELINE}" | awk '{print $1}')"
    if [[ "${expected}" != "${actual}" ]]; then
      echo "基线 SHA256 不一致：expected=${expected} actual=${actual}" >&2
      exit 1
    fi
    run_lint
    if ! command -v oasdiff >/dev/null 2>&1; then
      echo "未找到 oasdiff；请先安装：go install github.com/oasdiff/oasdiff@latest" >&2
      exit 1
    fi
    echo "== oasdiff breaking：基线 → 当前 =="
    # oasdiff 需比较两个自包含 bundle；直接用未 bundle 的入口会因 $ref/oneOf
    # 解析差异产生假阳性。
    CURRENT_BUNDLE="$(mktemp "${TMPDIR:-/tmp}/ddp-contract-current.XXXXXX.yaml")"
    trap 'rm -f "${CURRENT_BUNDLE}"' EXIT
    run_redocly bundle "${CURRENT}" -o "${CURRENT_BUNDLE}"
    oasdiff breaking "${BASELINE}" "${CURRENT_BUNDLE}" --fail-on ERR
    echo "兼容性检查通过：未发现 ERR 级破坏性变更。"
    ;;
esac
