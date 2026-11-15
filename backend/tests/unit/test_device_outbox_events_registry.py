"""R-13：设备域事件类型必须可被 AST 提取，且登记在归类表里。

背景：``event_type=f"device.{action}"`` 这类 f-string 在运行期才拼出事件名，
``scripts/check_outbox_events.py`` 的 AST 提取看不到它们，于是新增事件会
静默绕过"投递类必须有消费者 / 留痕类必须登记"的校验。

这些用例把三件事钉死：
1. 设备域源码里不再有 ``event_type=f"..."``；
2. 每个动作都能解析出具体的、形如 ``domain.action`` 的事件名；
3. 这些事件名出现在 ``docs/architecture/outbox事件语义界定-v1.0.md`` 的
   归类表里（留痕类），避免"代码有、文档没有"。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"
_DEVICES_DIR = _APP / "application" / "devices"
_DOC_PATH = _BACKEND.parent / "docs" / "architecture" / "outbox事件语义界定-v1.0.md"
# 与 scripts/check_outbox_events.py 保持一致：段内允许连字符，
# 因为生命周期动作名沿用 API 路径写法（report-fault / enter-maintenance）。
_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$")


def _device_modules() -> list[Path]:
    return sorted(_DEVICES_DIR.glob("*.py"))


def test_no_fstring_event_type_in_device_application() -> None:
    """``event_type=f"..."`` 在设备域必须为 0 处。"""
    offenders: list[str] = []
    for path in _device_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "event_type":
                    continue
                if isinstance(keyword.value, ast.JoinedStr):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"设备域仍有 f-string 事件类型：{offenders}"


def _event_constants(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Constant):
            continue
        if isinstance(value.value, str) and _EVENT_PATTERN.match(value.value):
            constants[target.id] = value.value
    return constants


def _mapping_entries(path: Path) -> dict[str, dict[str, str]]:
    """收集模块级 ``NAME: dict[str, str] = {"action": "domain.action"}`` 映射表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tables: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        entries: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                entries[key.value] = value.value
        if entries and all(_EVENT_PATTERN.match(item) for item in entries.values()):
            tables[node.target.id] = entries
    return tables


@pytest.mark.parametrize(
    ("module", "table", "expected_actions"),
    [
        ("devices.py", "DEVICE_ACTION_EVENTS", {"activate", "enter-maintenance", "report-fault"}),
        ("robots.py", "ROBOT_ACTION_EVENTS", {"activate", "report-fault", "retire"}),
        ("sensors.py", "SENSOR_ACTION_EVENTS", {"activate", "suspend", "retire"}),
    ],
)
def test_action_event_tables_cover_every_action(
    module: str,
    table: str,
    expected_actions: set[str],
) -> None:
    """动作到事件的映射必须覆盖全部动作名，不能只有键没有值。"""
    tables = _mapping_entries(_DEVICES_DIR / module)
    assert table in tables, f"{module} 缺少 {table} 映射表"
    entries = tables[table]

    assert expected_actions <= set(entries)
    assert all(_EVENT_PATTERN.match(value) for value in entries.values())


def test_calibration_event_constants_are_registered_in_semantics_doc() -> None:
    """标定域新增事件（G-11 / R-13）必须登记在归类表里。"""
    constants = _event_constants(_DEVICES_DIR / "calibrations.py")
    events = set(constants.values()) | set(
        _mapping_entries(_DEVICES_DIR / "calibrations.py")
        .get("_CALIBRATION_ACTION_EVENTS", {})
        .values()
    )

    assert events, "没有提取到任何标定事件"
    doc = _DOC_PATH.read_text(encoding="utf-8")
    missing = sorted(item for item in events if item not in doc)
    assert missing == [], f"以下标定事件未登记在 outbox 归类表：{missing}"


def test_device_domain_events_are_registered_in_semantics_doc() -> None:
    """device.* / sensor.* / robot.* 全部动作事件都必须出现在归类表。"""
    events: set[str] = set()
    for module in ("devices.py", "robots.py", "sensors.py"):
        events |= set(
            _mapping_entries(_DEVICES_DIR / module)
            .get(
                {
                    "devices.py": "DEVICE_ACTION_EVENTS",
                    "robots.py": "ROBOT_ACTION_EVENTS",
                    "sensors.py": "SENSOR_ACTION_EVENTS",
                }[module],
                {},
            )
            .values()
        )
        events |= set(_event_constants(_DEVICES_DIR / module).values())

    assert events
    doc = _DOC_PATH.read_text(encoding="utf-8")
    missing = sorted(item for item in events if item not in doc)
    assert missing == [], f"以下设备域事件未登记在 outbox 归类表：{missing}"
