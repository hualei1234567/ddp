"""跨数据集版本基线对比：API 行为与可追溯信息。

覆盖任务卡要求的五类用例：
1. 同数据集基线照旧允许（既有行为不变）；
2. 跨数据集但未声明 → 拒绝，断言 code 与文案；
3. 跨数据集且已声明、任务语义一致 → 放行，响应可读出版本信息；
4. 跨数据集且已声明、任务语义不一致 → 拒绝；
5. 跨项目基线 → 仍是 404（既有安全边界不能破）。

任务语义的唯一权威落点是 ``Model.task_type``，因此每个用例都显式建模型，
不靠夹具默认值——默认值一旦变化，用例会静默变成"两侧都取不到 → 拒绝"，
那条断言就再也证明不了"一致时放行"。
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.modelops_dependencies import get_artifact_inspector
from app.core.config import Settings

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version
from tests.api.test_modelops_api import (
    FakeInspector,
    _callback,
    _create_model,
    _ready_version,
    _settings,
)

# 与既有 modelops 用例同形的冻结数据集版本标识取法。
THRESHOLDS = {"success_rate": {"min": 0.9}}


def _client(factory, user_id, *, settings: Settings | None = None):
    client = make_client(factory, user_id, settings_override=settings or _settings())
    client.app.dependency_overrides[get_artifact_inspector] = lambda: FakeInspector(128)
    return client


def _model_with_task_type(client, project_id, owner_id, task_type: str) -> dict[str, object]:
    response = client.post(
        url(project_id, "/models"),
        json={
            "code": f"model-{uuid4().hex[:8]}",
            "name": f"{task_type} 模型",
            "task_type": task_type,
            "framework": "pytorch",
            "owner_id": str(owner_id),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _queue_evaluation(
    client,
    project_id,
    *,
    model_version_id: str,
    dataset_version_id: str,
    baseline_evaluation_id: str | None = None,
    cross_dataset_comparison: bool | None = None,
):
    payload: dict[str, object] = {
        "model_version_id": model_version_id,
        "dataset_version_id": dataset_version_id,
        "thresholds": THRESHOLDS,
    }
    if baseline_evaluation_id is not None:
        payload["baseline_evaluation_id"] = baseline_evaluation_id
    if cross_dataset_comparison is not None:
        payload["cross_dataset_comparison"] = cross_dataset_comparison
    return client.post(url(project_id, "/evaluations"), json=payload)


def _complete(client, project_id, evaluation_id: str, success_rate: float) -> None:
    response = _callback(
        client,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(project_id),
            "evaluation_id": evaluation_id,
            "metrics": {"success_rate": success_rate},
        },
    )
    assert response.status_code == 204, response.text


def test_same_dataset_baseline_still_allowed(api_db) -> None:
    """既有行为不变：同数据集版本基线，且未声明跨数据集，照旧 202。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    context = build_frozen_version(factory, ids)
    version = _ready_version(
        editor, ids.project_a, _create_model(editor, ids.project_a, ids.owner_a)["model_id"]
    )

    baseline = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
    )
    assert baseline.status_code == 202, baseline.text
    baseline_body = baseline.json()
    # 无基线时新字段为 null，不引入第二套语义。
    assert baseline_body["baseline_dataset_version_id"] is None

    second = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
        baseline_evaluation_id=baseline_body["evaluation_id"],
    )
    assert second.status_code == 202, second.text
    body = second.json()
    assert body["baseline_evaluation_id"] == baseline_body["evaluation_id"]
    # 同数据集时基线数据集版本就等于自己的数据集版本。
    assert body["baseline_dataset_version_id"] == str(context["version_id"])


def test_cross_dataset_without_declaration_is_rejected(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    first = build_frozen_version(factory, ids, seed_prefix="first", seed_salt="first-")
    second = build_frozen_version(factory, ids, seed_prefix="second", seed_salt="second-")
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    baseline = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(first["version_id"]),
    )
    assert baseline.status_code == 202, baseline.text

    rejected = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(second["version_id"]),
        baseline_evaluation_id=baseline.json()["evaluation_id"],
    )
    assert rejected.status_code == 422, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "evaluation_cross_dataset_not_declared"
    # 文案必须说清"需要显式声明"，否则调用方只能猜。
    assert "显式声明" in error["message"]
    # details 回显两个数据集版本，便于定位。
    assert error["details"]["baseline_dataset_version_id"] == str(first["version_id"])
    assert error["details"]["current_dataset_version_id"] == str(second["version_id"])


def test_cross_dataset_declared_with_matching_task_type_is_allowed(api_db) -> None:
    """显式声明 + 任务语义一致 → 放行，且响应能读出版本信息。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    old_dataset = build_frozen_version(factory, ids, seed_prefix="old", seed_salt="old-")
    new_dataset = build_frozen_version(factory, ids, seed_prefix="new", seed_salt="new-")

    # 模型升级：老版本与新版本同属 IMITATION（同一任务大类，可比）。
    old_model = _model_with_task_type(editor, ids.project_a, ids.owner_a, "IMITATION")
    new_model = _model_with_task_type(editor, ids.project_a, ids.owner_a, "IMITATION")
    old_version = _ready_version(editor, ids.project_a, old_model["model_id"])
    new_version = _ready_version(editor, ids.project_a, new_model["model_id"])

    baseline = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=old_version["model_version_id"],
        dataset_version_id=str(old_dataset["version_id"]),
    )
    assert baseline.status_code == 202, baseline.text
    baseline_id = baseline.json()["evaluation_id"]
    _complete(editor, ids.project_a, baseline_id, 0.72)

    current = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=new_version["model_version_id"],
        dataset_version_id=str(new_dataset["version_id"]),
        baseline_evaluation_id=baseline_id,
        cross_dataset_comparison=True,
    )
    assert current.status_code == 202, current.text
    body = current.json()
    assert body["baseline_evaluation_id"] == baseline_id
    # 可追溯：基线用的是哪个数据集版本、当前是哪个，都能从响应读出来。
    assert body["baseline_dataset_version_id"] == str(old_dataset["version_id"])
    assert body["dataset_version_id"] == str(new_dataset["version_id"])
    assert body["baseline_dataset_version_id"] != body["dataset_version_id"]

    # 落库后再读一次，确认不是只在响应体里临时拼出来的。
    stored = editor.get(url(ids.project_a, f"/evaluations/{body['evaluation_id']}")).json()
    assert stored["baseline_dataset_version_id"] == str(old_dataset["version_id"])


def test_cross_dataset_declared_but_task_type_mismatch_is_rejected(api_db) -> None:
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    old_dataset = build_frozen_version(factory, ids, seed_prefix="old", seed_salt="old-")
    new_dataset = build_frozen_version(factory, ids, seed_prefix="new", seed_salt="new-")

    imitation = _model_with_task_type(editor, ids.project_a, ids.owner_a, "IMITATION")
    vlm = _model_with_task_type(editor, ids.project_a, ids.owner_a, "VLM")
    imitation_version = _ready_version(editor, ids.project_a, imitation["model_id"])
    vlm_version = _ready_version(editor, ids.project_a, vlm["model_id"])

    baseline = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=imitation_version["model_version_id"],
        dataset_version_id=str(old_dataset["version_id"]),
    )
    assert baseline.status_code == 202, baseline.text

    rejected = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=vlm_version["model_version_id"],
        dataset_version_id=str(new_dataset["version_id"]),
        baseline_evaluation_id=baseline.json()["evaluation_id"],
        cross_dataset_comparison=True,
    )
    assert rejected.status_code == 422, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "evaluation_not_comparable"
    details = error["details"]
    assert details["reason"] == "task_type_mismatch"
    assert details["baseline_task_type"] == "IMITATION"
    assert details["current_task_type"] == "VLM"


def test_cross_dataset_flag_without_baseline_is_rejected(api_db) -> None:
    """声明了跨数据集却没有基线：不能静默忽略，否则调用方以为声明生效了。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    context = build_frozen_version(factory, ids)
    version = _ready_version(
        editor, ids.project_a, _create_model(editor, ids.project_a, ids.owner_a)["model_id"]
    )
    response = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
        cross_dataset_comparison=True,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_cross_project_baseline_still_rejected(api_db) -> None:
    """既有安全边界不变：基线属于其他项目 → 拒绝，且不泄漏存在性。

    这条断言记录的是**改造前就有的行为**（原实现即抛
    ``EvaluationBaselineInvalidError("基线评测不存在或跨项目")`` → 422
    ``evaluation_baseline_invalid``）。本次只验证它没被跨数据集放开冲掉：
    "基线不存在" 与 "基线跨项目" 返回同一个 code 与同一句文案，调用方无法
    据此判断该 ID 在别的项目里是否真实存在——这是边界有效的前提。
    """
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])

    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.models.identity import ProjectMember

    # 让 editor 同时是 B 项目的编辑者，因此"跨项目基线"不是权限 403，
    # 而是项目隔离层面的"基线不可见"——恰好是要验证的那条边界。
    with factory() as session:
        session.add(
            ProjectMember(
                project_id=ids.project_b,
                user_id=ids.editor_a,
                role=ProjectRole.EDITOR.value,
            )
        )
        session.commit()

    foreign_ids = ApiIds(
        project_a=ids.project_b,
        project_b=ids.project_a,
        editor_a=ids.editor_a,
        owner_a=ids.owner_a,
        viewer_a=ids.owner_a,
        outsider=ids.outsider,
    )
    foreign = build_frozen_version(factory, foreign_ids)
    foreign_model = _model_with_task_type(editor, ids.project_b, ids.owner_a, "IMITATION")
    foreign_version = _ready_version(editor, ids.project_b, foreign_model["model_id"])
    foreign_eval = _queue_evaluation(
        editor,
        ids.project_b,
        model_version_id=foreign_version["model_version_id"],
        dataset_version_id=str(foreign["version_id"]),
    )
    assert foreign_eval.status_code == 202, foreign_eval.text

    response = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
        baseline_evaluation_id=foreign_eval.json()["evaluation_id"],
        cross_dataset_comparison=True,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "evaluation_baseline_invalid"

    # 关键：与"基线根本不存在"返回完全相同的结果——否则攻击者可用返回值
    # 探测某个评测 ID 是否存在于别的项目。
    ghost = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
        baseline_evaluation_id=str(uuid4()),
        cross_dataset_comparison=True,
    )
    assert ghost.status_code == response.status_code
    assert ghost.json()["error"]["code"] == response.json()["error"]["code"]
    assert ghost.json()["error"]["message"] == response.json()["error"]["message"]


def test_idempotency_fingerprint_covers_baseline(api_db) -> None:
    """同一幂等键、仅基线不同 → 必须视为不同请求，而非静默重放。

    此前幂等指纹漏了 ``baseline_evaluation_id``：第二次请求会被当成重放，
    返回第一次的评测，客户端拿到的对象与请求体不符且没有任何报错。
    """
    factory, ids = api_db
    editor = _client(factory, ids.editor_a)
    context = build_frozen_version(factory, ids)
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    first_baseline = _queue_evaluation(
        editor,
        ids.project_a,
        model_version_id=version["model_version_id"],
        dataset_version_id=str(context["version_id"]),
    )
    assert first_baseline.status_code == 202, first_baseline.text

    key = f"idem-{uuid4().hex[:12]}"
    payload = {
        "model_version_id": version["model_version_id"],
        "dataset_version_id": str(context["version_id"]),
        "thresholds": THRESHOLDS,
    }
    without_baseline = editor.post(
        url(ids.project_a, "/evaluations"), json=payload, headers={"Idempotency-Key": key}
    )
    assert without_baseline.status_code == 202, without_baseline.text

    with_baseline = editor.post(
        url(ids.project_a, "/evaluations"),
        json={**payload, "baseline_evaluation_id": first_baseline.json()["evaluation_id"]},
        headers={"Idempotency-Key": key},
    )
    assert with_baseline.status_code == 409, with_baseline.text
    assert with_baseline.json()["error"]["code"] == "idempotency_key_reused"
