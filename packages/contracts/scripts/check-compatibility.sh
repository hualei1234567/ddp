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

# 声明为「开放集合」的枚举不参与破坏性判定。
#
# 为什么需要这一步：这些枚举是被大量响应共享的**可增长**取值集合。新增一个值会让
# oasdiff 对每一个引用它的操作各报一条 `response-property-enum-value-added`——
# 本次新增 4 个错误码直接产出 5508 条，把真正需要关注的破坏性变更埋掉了。
#
# 契约本身已经写明客户端必须容错（错误码：「未知错误不得默认成功」；血缘节点类型：
# 新接入方会带来新节点类型）。因此这类增长按设计不算破坏性变更。
#
# 做法：比较前把**两侧**同名枚举归一成开放字符串。于是这些枚举的取值增删不误报，
# 而它们的**结构变化**（变成对象、删掉字段）、以及所有其它枚举（状态机、类型、
# 分区策略）的新增值仍然会被拦截。
#
# 清单必须显式列出并且逐个写明理由——不允许用通配或「全部枚举」开关。
# LineageRelation：血缘关系随接入方增长（最近新增 TRAINED_ON），契约已用
# x-extensible-enum 明示客户端必须容忍未知关系；新增取值不是破坏性变更。
# 说明：oasdiff 的 `--err-ignore` 在 `breaking` 子命令下实测无效（连 `.*` 都不忽略），
# 因此用预处理而不是忽略规则。1.37.0 与 CI 的 1.38.0 行为一致。
OPEN_ENUMS="ErrorCode LineageNodeType LineageRelation"
prepare_open_enums() {
  local root="$1"
  python3 - "$root" "$OPEN_ENUMS" <<'PYEOF'
import pathlib
import sys

import yaml

root = pathlib.Path(sys.argv[1])
wanted = set(sys.argv[2].split())
targets = 0
for fragment in sorted((root / "fragments").glob("*.yaml")):
    with fragment.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        continue
    changed = False
    for name in wanted & set(document):
        schema = document[name]
        if isinstance(schema, dict) and "enum" in schema:
            schema.pop("enum", None)
            schema["type"] = "string"
            changed = True
            targets += 1
    if changed:
        with fragment.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, allow_unicode=True, sort_keys=False)
if targets == 0:
    print("警告：未在任何 fragment 中找到声明的开放枚举", file=sys.stderr)
PYEOF
}

prepared_current="${tmpdir}/current-contract"
cp -R "$(dirname "${current}")" "${prepared_current}"
prepare_open_enums "${tmpdir}/packages/contracts"
prepare_open_enums "${prepared_current}"

oasdiff breaking "${base_doc}" "${prepared_current}/openapi.yaml" --fail-on ERR
