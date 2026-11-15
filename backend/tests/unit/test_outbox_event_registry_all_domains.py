"""R-13 全仓收尾：所有域的事件类型都必须可被 AST 提取，且映射表覆盖真实动作。

背景：``event_type=f"dataset.export.{action}"`` 这类 f-string 在运行期才拼出
事件名，``scripts/check_outbox_events.py`` 的 AST 提取看不到它们，事件因而
静默逃过归类校验。设备域此前已改为模块级映射表；本文件把同一约束扩展到
数据集、数据源、摄入、modelops、供应商五域。

钉死的三件事：

1. ``backend/app`` 里不再有 ``event_type=f"..."``（脚本守卫之外的独立断言，
   避免脚本被误改后失去保护）；
2. 每张动作→事件映射表的**键集合**等于对应域状态机的动作白名单——
   状态机加动作而映射没跟上时，运行期会 ``KeyError``，这里提前失败；
3. 映射值形如 ``domain.action``，且与「原 f-string 前缀 + 键」逐字一致——
   事件名是可观测契约，改了会让已落库的 outbox 记录与历史消费者失配。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from app.application.datasets import (
    dataset_grants,
    exports,
    training_deliveries,
    training_references,
    training_requests,
)
from app.application.datasources import events as datasource_events
from app.application.episodes import episodes as episode_service
from app.application.ingestion import jobs as ingestion_jobs
from app.application.modelops import (
    evaluations,
    feedback,
    inference,
    model_registry,
    requirements,
)
from app.application.suppliers import inbound, supplier_directory
from app.domain.datasets import state_machines as dataset_state_machines
from app.domain.datasets import training_state_machines
from app.domain.datasources import state_machines as datasource_state_machines
from app.domain.episodes import state_machines as episode_state_machines
from app.domain.modelops import state_machines as modelops_state_machines
from app.domain.suppliers import state_machines as supplier_state_machines

_BACKEND = Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"
_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$")


def _action_names(rules: dict[str, dict[str, object]]) -> set[str]:
    """状态机规则表里的全部动作名。"""
    return {action for transitions in rules.values() for action in transitions}


def test_no_fstring_event_type_anywhere_in_app() -> None:
    """``event_type=f"..."`` 在整个 backend/app 必须为 0 处。"""
    offenders: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "event_type" and isinstance(keyword.value, ast.JoinedStr):
                    offenders.append(f"{path.relative_to(_BACKEND)}:{node.lineno}")
    assert offenders == [], f"仍有 f-string 事件类型：{offenders}"


@pytest.mark.parametrize(
    ("label", "table", "expected_actions", "prefix"),
    [
        (
            "datasource.import",
            datasource_events.DATASOURCE_IMPORT_ACTION_EVENTS,
            set(datasource_state_machines._IMPORT_ACTIONS),
            "datasource.import.",
        ),
        (
            "ingestion.job",
            ingestion_jobs.INGESTION_JOB_ACTION_EVENTS,
            # ``apply_action`` 的显式分支；未知动作在此之前就抛错。
            {
                "worker-start",
                "worker-succeed",
                "worker-fail",
                "job-timeout",
                "retry",
                "cancel",
                "worker-cancelled",
            },
            "ingestion.job.",
        ),
        (
            "ingestion.episode",
            episode_service.INGESTION_EPISODE_ACTION_EVENTS,
            # 状态机动作 + 应用层独有的 ``record-quality``（领域方法不查状态机）
            # + ``ready``（仅经 ``mark_ready`` 的内部调用点产生）。
            _action_names(episode_state_machines._EPISODE_RULES) | {"record-quality", "ready"},
            "ingestion.episode.",
        ),
        (
            "dataset.grant",
            dataset_grants.DATASET_GRANT_ACTION_EVENTS,
            _action_names(training_state_machines._GRANT_RULES) & {"approve", "reject", "revoke"},
            "dataset.grant.",
        ),
        (
            "dataset.export",
            exports.EXPORT_ACTION_EVENTS,
            _action_names(dataset_state_machines._EXPORT_RULES) & {"cancel", "retry"},
            "dataset.export.",
        ),
        (
            "training.delivery",
            training_deliveries.TRAINING_DELIVERY_ACTION_EVENTS,
            {
                "ready",
                "deliver",
                "accept",
                "reject",
                "rework",
                "resume-ready",
                "cancel",
            },
            "training.delivery.",
        ),
        (
            "training.external_reference",
            training_references.EXTERNAL_REFERENCE_ACTION_EVENTS,
            {"sync", "cancel"},
            "training.external_reference.",
        ),
        (
            "training.request.revision_",
            training_requests.TRAINING_REVISION_ACTION_EVENTS,
            {"submit", "approve", "reject"},
            "training.request.revision_",
        ),
        (
            "training.request.",
            training_requests.TRAINING_REQUEST_ACTION_EVENTS,
            {"block", "unblock", "cancel", "close"},
            "training.request.",
        ),
        (
            "modelops.data_requirement",
            requirements.DATA_REQUIREMENT_ACTION_EVENTS,
            _action_names(modelops_state_machines._REQUIREMENT) | {"link-retest"},
            "modelops.data_requirement.",
        ),
        (
            "modelops.inference_service",
            inference.INFERENCE_SERVICE_ACTION_EVENTS,
            _action_names(modelops_state_machines._SERVICE),
            "modelops.inference_service.",
        ),
        (
            "modelops.feedback",
            feedback.FEEDBACK_ACTION_EVENTS,
            _action_names(modelops_state_machines._FEEDBACK),
            "modelops.feedback.",
        ),
        (
            "modelops.evaluation",
            evaluations.EVALUATION_ACTION_EVENTS,
            {"cancel", "retry"},
            "modelops.evaluation.",
        ),
        (
            "modelops.model",
            model_registry.MODEL_ACTION_EVENTS,
            _action_names(modelops_state_machines._MODEL),
            "modelops.model.",
        ),
        (
            "modelops.model_version",
            model_registry.MODEL_VERSION_ACTION_EVENTS,
            _action_names(modelops_state_machines._VERSION),
            "modelops.model_version.",
        ),
        (
            "supplier",
            supplier_directory.SUPPLIER_ACTION_EVENTS,
            _action_names(supplier_state_machines._SUPPLIER),
            "supplier.",
        ),
        (
            "supplier.batch",
            inbound.DELIVERY_BATCH_ACTION_EVENTS,
            _action_names(supplier_state_machines._BATCH),
            "supplier.batch.",
        ),
        (
            "supplier.segment",
            inbound.INBOUND_SEGMENT_ACTION_EVENTS,
            _action_names(supplier_state_machines._SEGMENT),
            "supplier.segment.",
        ),
    ],
)
def test_action_event_table_covers_state_machine_actions(
    label: str,
    table: dict[str, str],
    expected_actions: set[str],
    prefix: str,
) -> None:
    """映射键集合必须等于可达动作集合，且值 = 前缀 + 键（事件名不变）。"""
    assert set(table) == expected_actions, (
        f"{label} 映射缺少 {sorted(expected_actions - set(table))}，"
        f"多出 {sorted(set(table) - expected_actions)}"
    )
    for action, event_type in table.items():
        assert _EVENT_PATTERN.match(event_type), f"{label}.{action} 事件名形状非法：{event_type}"
        assert (
            event_type == f"{prefix}{action}"
        ), f"{label}.{action} 事件名被改写：{event_type} != {prefix}{action}"


def test_policy_created_events_preserve_original_names() -> None:
    """策略创建事件此前是 ``f"{resource_type.lower()}.created"``，名字必须不变。"""
    from app.application.datasets import policies

    table = policies.POLICY_CREATED_EVENTS
    assert set(table) == {"DEDUP_POLICY", "LEAKAGE_POLICY", "QUALITY_POLICY", "SPLIT_POLICY"}
    for resource_type, event_type in table.items():
        assert event_type == f"{resource_type.lower()}.created"


def test_no_event_type_is_silently_filtered() -> None:
    """事件首段必须都在脚本白名单里，否则会被归类校验静默丢弃。

    ``check_outbox_events.py`` 按首段过滤，白名单外的会被无声丢掉——既不计入
    总数，也不会因"未归类"失败。历史上 `robot.*` / `calibration.*` /
    `*_policy.created` 共 19 个事件正是这样漏掉的。

    这里复刻脚本的判定口径（只认 `event_type=` 位置与 `*_EVENTS` 映射表的值），
    断言没有任何事件名被挡在白名单外。
    """
    # 脚本在仓库根的 `scripts/`，不在 backend 的 import 路径里，用路径加载。
    import importlib.util

    script = _BACKEND.parent / "scripts" / "check_outbox_events.py"
    spec = importlib.util.spec_from_file_location("check_outbox_events", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    domains = module._EVENT_DOMAINS
    pattern = module._EVENT_PATTERN

    found: set[str] = set()
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # event_type= 关键字位置
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "event_type":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
        # 模块级 *_EVENTS 映射表的值
        for node in tree.body:
            target = None
            value_node = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
                target, value_node = node.target, node.value
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                target = node.targets[0] if node.targets else None
                value_node = node.value
            if value_node is None or not isinstance(target, ast.Name):
                continue
            if not target.id.endswith("_EVENTS"):
                continue
            for item in value_node.values:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    found.add(item.value)

    filtered = {item for item in found if pattern.match(item) and item.split(".")[0] not in domains}
    assert filtered == set(), (
        f"这些事件名会被 check_outbox_events.py 静默过滤：{sorted(filtered)}；"
        "确属事件请补 _EVENT_DOMAINS 首段，并在归类文档登记"
    )
