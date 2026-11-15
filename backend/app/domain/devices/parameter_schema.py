"""分类型标定参数校验（G-19 / ADR-0009）。

参数形状由**契约文件**定义，不在代码里硬编码：
`docs/architecture/contract-draft/device/calibration-parameters.v1.schema.json`
是唯一权威。代码只负责"按 calibration_type 选对 $defs 并校验"。

为什么引入 ``jsonschema`` 而不是手写校验：该 schema 用了 allOf / $ref /
enum / 数值约束，手写等价实现既容易漏分支，又无法与契约保持同步
（`CLAUDE.md` 第二节：不得手写已知有标准解法的底层能力）。

校验失败要给出**可定位**的信息：字段路径 + 原因，而不是笼统的"参数非法"——
标定工程师需要知道改哪个字段。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.domain.devices.enums import CalibrationType
from app.domain.devices.errors import DomainValidationError

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs/architecture/contract-draft/device/calibration-parameters.v1.schema.json"
)
SCHEMA_VERSION = "ddp.device.calibration-parameters/1.0.0"


@lru_cache(maxsize=1)
def _base_schema() -> Mapping[str, object]:
    """读取并缓存契约 schema；文件缺失是配置错误，必须显式失败。"""
    if not _SCHEMA_PATH.is_file():
        raise DomainValidationError(f"标定参数契约文件缺失：{_SCHEMA_PATH}；无法校验参数形状")
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=len(CalibrationType))
def _validator_for(calibration_type: str) -> Draft202012Validator:
    """按类型构造 validator：把该类型的 $defs 提为根，其余共用定义保留。"""
    base = _base_schema()
    defs = base.get("$defs") or {}
    definition = defs.get(calibration_type)
    if not isinstance(definition, dict):
        raise DomainValidationError(f"契约未定义 calibration_type={calibration_type} 的参数形状")
    # 组装一个只含该类型定义的根 schema，$defs 保留以便 $ref 解析。
    root = {
        "$schema": base.get("$schema"),
        "$id": f"{base.get('$id', '')}#{calibration_type}",
        "$defs": defs,
        **definition,
    }
    return Draft202012Validator(root)


def validate_parameters(
    calibration_type: CalibrationType | str,
    parameters: Mapping[str, object],
) -> None:
    """按类型校验参数；失败抛 ``DomainValidationError``，消息含字段路径。

    ``details`` 同时携带结构化的 ``field_path``：客户端要能直接高亮出错的
    输入框，靠解析中文消息里的 ``a/b/c`` 既脆弱又不可靠（G-19）。
    """
    type_name = (
        calibration_type.value
        if isinstance(calibration_type, CalibrationType)
        else str(calibration_type)
    )
    validator = _validator_for(type_name)
    # 领域层传入的是 MappingProxyType（冻结视图），jsonschema 只认 dict/object；
    # 这里做一次浅解包，语义不变——校验是只读操作。
    payload = _unfreeze(parameters)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    first = errors[0]
    raise DomainValidationError(
        _describe(first, type_name, len(errors)),
        details={
            "calibration_type": type_name,
            "field_path": _field_path(first),
            "error_count": len(errors),
        },
    )


def _field_path(error: ValidationError) -> list[str]:
    """把 jsonschema 的 ``absolute_path`` 转成纯字符串片段列表。

    根级错误返回空列表——客户端据此知道"问题不在某个具体字段"，
    而不是收到一个假字段名。
    """
    return [str(item) for item in error.absolute_path]


def _describe(error: ValidationError, type_name: str, total: int) -> str:
    path = "/".join(str(item) for item in error.absolute_path) or "(根)"
    suffix = f"（共 {total} 处不合法，仅显示第一处）" if total > 1 else ""
    return f"{type_name} 参数不合法：{path} — {error.message}{suffix}"


def _unfreeze(value: object) -> object:
    """把 MappingProxyType / tuple 递归转成普通 dict / list，供 jsonschema 校验。"""
    if isinstance(value, Mapping):
        return {str(key): _unfreeze(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_unfreeze(item) for item in value]
    return value
