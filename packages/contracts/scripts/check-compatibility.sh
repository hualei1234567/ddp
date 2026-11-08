#!/usr/bin/env bash
# OpenAPI 破坏性兼容性检查。
#
# 用法：
#   bash packages/contracts/scripts/check-compatibility.sh [当前契约路径] [基线 git 引用]
#
# 基线可以是 commit SHA、分支或 HEAD^。脚本会把基线版本的 packages/contracts
# 完整导出到临时目录，再与工作区当前契约比较；因此同时支持自包含契约与
# fragments/ 拆分契约。首次提交/无基线时跳过并给出提示，不把“缺少历史”误判为兼容。
# oasdiff 由 CI 安装；本地需自行 `go install github.com/oasdiff/oasdiff@latest`。
set -euo pipefail

current="${1:-packages/contracts/openapi.yaml}"
base_ref="${2:-${BASE_REF:-}}"

if [[ -z "${base_ref}" ]]; then
  echo "未提供基线引用（BASE_REF/参数），跳过兼容性 diff。"
  exit 0
fi

if [[ "${base_ref}" =~ ^0+$ ]]; then
  echo "基线引用为全零（新分支首次 push），跳过兼容性 diff。"
  exit 0
fi

if ! command -v oasdiff >/dev/null 2>&1; then
  echo "未找到 oasdiff；请先安装：go install github.com/oasdiff/oasdiff@latest" >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

if ! git archive "${base_ref}" packages/contracts 2>/dev/null | tar -x -C "${tmpdir}"; then
  echo "基线 ${base_ref} 中没有可导出的 packages/contracts，视为首次契约提交，跳过兼容性 diff。"
  exit 0
fi

base_doc="${tmpdir}/packages/contracts/openapi.yaml"
if [[ ! -f "${base_doc}" ]]; then
  echo "基线 ${base_ref} 中不存在 packages/contracts/openapi.yaml，跳过兼容性 diff。"
  exit 0
fi

echo "比较基线 ${base_ref}:packages/contracts/openapi.yaml → ${current}"
echo "基线已导出到 ${tmpdir}/packages/contracts（含 fragments）"

read_info_version() {
  awk '/^info:/{inside=1; next} inside && /^  version:/{print $2; exit}' "$1"
}
base_version="$(read_info_version "${base_doc}")"
current_version="$(read_info_version "${current}")"
base_major="${base_version%%.*}"
current_major="${current_version%%.*}"
if [[ -n "${base_version}" && -n "${current_version}" && "${base_major}" != "${current_major}" ]]; then
  echo "主版本变化 ${base_version} → ${current_version}：按版本策略允许破坏性变更，跳过 oasdiff breaking。"
  exit 0
fi

oasdiff breaking "${base_doc}" "${current}" --fail-on ERR
