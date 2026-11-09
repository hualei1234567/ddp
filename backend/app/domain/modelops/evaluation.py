"""评测指标与结论计算。

规则：阈值指标缺失 → UNKNOWN；任一阈值失败 → FAIL；全部满足 → PASS；
没有阈值时只要有指标 → UNKNOWN（无法判定通过），不得默认 PASS。
"""

from __future__ import annotations

from app.domain.modelops.enums import EvaluationVerdict
from app.domain.modelops.errors import EvaluationMetricsInvalidError
from app.domain.modelops.validation import number_map, text_value


def parse_thresholds(value: object) -> dict[str, dict[str, float | None]]:
    if not isinstance(value, dict) or not value:
        raise EvaluationMetricsInvalidError("thresholds 必须是非空对象")
    result: dict[str, dict[str, float | None]] = {}
    for key, raw in value.items():
        name = text_value(key, "thresholds.key", max_length=100)
        if not isinstance(raw, dict):
            raise EvaluationMetricsInvalidError(f"thresholds.{name} 必须是对象")
        entry: dict[str, float | None] = {"min": None, "max": None}
        for bound in ("min", "max"):
            bound_value = raw.get(bound)
            if bound_value is None:
                continue
            if not isinstance(bound_value, int | float) or isinstance(bound_value, bool):
                raise EvaluationMetricsInvalidError(f"thresholds.{name}.{bound} 必须是数字")
            entry[bound] = float(bound_value)
        if entry["min"] is None and entry["max"] is None:
            raise EvaluationMetricsInvalidError(f"thresholds.{name} 至少需要 min 或 max")
        result[name] = entry
    return result


def parse_metrics(value: object) -> dict[str, float]:
    if value is None:
        return {}
    return number_map(value, "metrics")


def compute_verdict(
    metrics: dict[str, float],
    thresholds: dict[str, dict[str, float | None]],
) -> str:
    if not thresholds:
        return EvaluationVerdict.UNKNOWN.value
    for name, bounds in thresholds.items():
        if name not in metrics:
            return EvaluationVerdict.UNKNOWN.value
        value = metrics[name]
        minimum = bounds.get("min")
        maximum = bounds.get("max")
        if minimum is not None and value < minimum:
            return EvaluationVerdict.FAIL.value
        if maximum is not None and value > maximum:
            return EvaluationVerdict.FAIL.value
    return EvaluationVerdict.PASS.value
