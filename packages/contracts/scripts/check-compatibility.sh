#!/usr/bin/env bash
# OpenAPI 破坏性兼容性检查。
#
# 用法：
#   bash packages/contracts/scripts/check-compatibility.sh [当前契约路径] [基线 git 引用]
#
# 基线可以是 commit SHA、分支或 HEAD^。首次提交/无基线时跳过并给出提示，
# 不把“缺少历史”误判为兼容。oasdiff 由 CI 安装；本地需自行 `go install`。
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

if ! git cat-file -e "${base_ref}:${current}" 2>/dev/null; then
  echo "基线 ${base_ref} 中不存在 ${current}，视为首次契约提交，跳过兼容性 diff。"
  exit 0
fi

if ! command -v oasdiff >/dev/null 2>&1; then
  echo "未找到 oasdiff；请先安装：go install github.com/oasdiff/oasdiff@latest" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT
git show "${base_ref}:${current}" > "${tmp}"

echo "比较基线 ${base_ref}:${current} → ${current}"
oasdiff breaking "${tmp}" "${current}" --fail-on ERR
