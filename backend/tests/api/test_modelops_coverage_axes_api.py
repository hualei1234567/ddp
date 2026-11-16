"""缺口视图三个封闭轴（ACTION / LABEL / MODALITY）的接口级验收。

守两条接口契约：

1. 有权威来源时 ``coverage_source=MEASURED``，``current_count`` 用实测值覆盖
   人工值；封闭轴总体完整时，数据里缺席的维度值是真实的 0（MEASURED + 0）；
2. 总体不完整时整轴回退：有人工值 → ``MANUAL``，两者都无 → ``None`` + ``UNKNOWN``，
   ``suggested_episode_count`` 不得臆测。
"""

from __future__ import annotations

from uuid import UUID

from tests.api.conftest import make_client
from tests.api.dataset_support import url
from tests.api.modelops_support import FakeInspector
from tests.api.modelops_support import client as _client
from tests.api.modelops_support import create_model as _create_model
from tests.api.modelops_support import feedback_with_dimension as _feedback_with_dimension
from tests.api.modelops_support import ready_version as _ready_version
from tests.infrastructure.annotation_chain_support import approve_episodes
from tests.infrastructure.coverage_support import (
    add_action_spec,
    add_collection_task,
    add_robot,
    add_sensor,
    episode_row,
    insert_episodes,
)


def _feedback_version(factory, ids):
    editor = _client(factory, ids.editor_a, FakeInspector(128))
    model = _create_model(editor, ids.project_a, ids.owner_a)
    version = _ready_version(editor, ids.project_a, model["model_id"])
    return editor, version["model_version_id"]


def _gaps(client, project_id: UUID, **params) -> dict:
    response = client.get(url(project_id, "/data-gaps"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _item(gaps: dict, dimension_value: str) -> dict:
    return next(item for item in gaps["items"] if item["dimension_value"] == dimension_value)


# --------------------------------------------------------------------------- #
# LABEL
# --------------------------------------------------------------------------- #
def test_data_gaps_measures_label_coverage(api_db) -> None:
    factory, ids = api_db
    episodes = [episode_row(ids.project_a, ids.editor_a) for _ in range(2)]
    insert_episodes(factory, episodes)
    with factory() as session:
        approve_episodes(
            session,
            ids.project_a,
            ids.editor_a,
            labels_by_episode={
                episodes[0].id: [{"label_key": "grasp"}],
                episodes[1].id: [{"label_key": "grasp"}, {"label_key": "place"}],
            },
        )
        session.commit()

    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="LABEL",
        dimension_value="grasp",
        current_count=9,
        target_count=10,
    )
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="LABEL",
        dimension_value="grasp-cup",
        target_count=4,
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="LABEL")

    grasp = _item(gaps, "grasp")
    assert grasp["coverage_source"] == "MEASURED"
    assert grasp["current_count"] == 2
    assert grasp["suggested_episode_count"] == 8
    # 全部 Episode 都已批准标注且都没有该标签 → 实测 0，不是 UNKNOWN。
    missing = _item(gaps, "grasp-cup")
    assert missing["coverage_source"] == "MEASURED"
    assert missing["current_count"] == 0
    assert missing["suggested_episode_count"] == 4
    # 有实测时间戳（标注投影更新了 Episode），不得报 UNKNOWN。
    assert gaps["freshness"] != "UNKNOWN"


def test_data_gaps_label_falls_back_to_manual_when_population_incomplete(api_db) -> None:
    factory, ids = api_db
    annotated = episode_row(ids.project_a, ids.editor_a)
    insert_episodes(factory, [annotated, episode_row(ids.project_a, ids.editor_a)])
    with factory() as session:
        approve_episodes(
            session,
            ids.project_a,
            ids.editor_a,
            labels_by_episode={annotated.id: [{"label_key": "grasp"}]},
        )
        session.commit()

    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="LABEL",
        dimension_value="grasp",
        current_count=3,
        target_count=12,
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="LABEL")
    label = _item(gaps, "grasp")
    assert label["coverage_source"] == "MANUAL"
    assert label["current_count"] == 3
    assert label["suggested_episode_count"] == 9
    assert gaps["freshness"] == "UNKNOWN"


# --------------------------------------------------------------------------- #
# ACTION
# --------------------------------------------------------------------------- #
def test_data_gaps_measures_action_coverage(api_db) -> None:
    factory, ids = api_db
    with factory() as session:
        spec = add_action_spec(
            session,
            ids.project_a,
            ids.editor_a,
            actions=[{"code": "pick", "name": "抓取"}, {"code": "place", "name": "放置"}],
        )
        task = add_collection_task(session, ids.project_a, ids.editor_a, action_spec_id=spec)
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(ids.project_a, ids.editor_a, task_id=task),
            episode_row(ids.project_a, ids.editor_a, task_id=task),
            episode_row(ids.project_a, ids.editor_a, task_id=task),
        ],
    )
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="ACTION",
        dimension_value="pick",
        current_count=1,
        target_count=5,
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="ACTION")
    pick = _item(gaps, "pick")
    assert pick["coverage_source"] == "MEASURED"
    assert pick["current_count"] == 3
    assert pick["suggested_episode_count"] == 2


# --------------------------------------------------------------------------- #
# MODALITY
# --------------------------------------------------------------------------- #
def test_data_gaps_measures_modality_coverage(api_db) -> None:
    factory, ids = api_db
    with factory() as session:
        robot = add_robot(session, ids.project_a, ids.editor_a)
        rgb = add_sensor(session, ids.project_a, ids.editor_a, robot_id=robot, sensor_type="RGB")
        depth = add_sensor(
            session, ids.project_a, ids.editor_a, robot_id=robot, sensor_type="DEPTH"
        )
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(ids.project_a, ids.editor_a, sensor_ids=[str(rgb)]),
            episode_row(ids.project_a, ids.editor_a, sensor_ids=[str(rgb)]),
            episode_row(ids.project_a, ids.editor_a, sensor_ids=[str(depth)]),
        ],
    )
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="MODALITY",
        dimension_value="RGB",
        target_count=5,
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="MODALITY")
    rgb_item = _item(gaps, "RGB")
    assert rgb_item["coverage_source"] == "MEASURED"
    assert rgb_item["current_count"] == 2
    assert rgb_item["suggested_episode_count"] == 3


# --------------------------------------------------------------------------- #
# 不可测 → UNKNOWN / MANUAL
# --------------------------------------------------------------------------- #
def test_data_gaps_closed_axes_unknown_without_authoritative_source(api_db) -> None:
    factory, ids = api_db
    # Episode 没有 task_id、没有 sensor_ids、没有标注 → 三个封闭轴都不可测。
    insert_episodes(factory, [episode_row(ids.project_a, ids.editor_a)])
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="ACTION",
        dimension_value="pick",
        target_count=3,
    )
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="MODALITY",
        dimension_value="RGB",
        current_count=2,
        target_count=5,
    )
    gaps = _gaps(editor, ids.project_a)
    action = _item(gaps, "pick")
    assert action["coverage_source"] == "UNKNOWN"
    assert action["current_count"] is None
    assert action["suggested_episode_count"] == 0
    modality = _item(gaps, "RGB")
    assert modality["coverage_source"] == "MANUAL"
    assert modality["current_count"] == 2
    assert modality["suggested_episode_count"] == 3


def test_data_gaps_closed_axes_respect_project_isolation(api_db) -> None:
    """另一个项目的完整权威数据不得让本项目从 UNKNOWN 变成 MEASURED。"""
    factory, ids = api_db
    with factory() as session:
        robot = add_robot(session, ids.project_b, ids.owner_a)
        rgb = add_sensor(session, ids.project_b, ids.owner_a, robot_id=robot, sensor_type="RGB")
        session.commit()
    insert_episodes(
        factory,
        [episode_row(ids.project_b, ids.owner_a, sensor_ids=[str(rgb)])],
    )
    editor, version_id = _feedback_version(factory, ids)
    _feedback_with_dimension(
        editor,
        ids.project_a,
        version_id,
        dimension_type="MODALITY",
        dimension_value="RGB",
        target_count=5,
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="MODALITY")
    rgb_item = _item(gaps, "RGB")
    assert rgb_item["coverage_source"] == "UNKNOWN"
    assert rgb_item["current_count"] is None


# --------------------------------------------------------------------------- #
# 零反馈项目的追溯（真实缺陷回归）
# --------------------------------------------------------------------------- #
def test_data_gaps_returns_measured_items_when_no_feedback_exists(api_db) -> None:
    """项目有 Episode、零反馈记录时，data-gaps 必须返回实测维度而不是空数组。

    此前 ``aggregate_gaps`` 只从人工反馈建桶，实测覆盖被算出来又丢掉，接口返回
    ``{"items": [], "freshness": "UNKNOWN"}``，界面读成"没有任何数据"。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    insert_episodes(
        factory,
        [
            episode_row(ids.project_a, ids.editor_a, scene_labels=["industrial_workspace"]),
            episode_row(ids.project_a, ids.editor_a, scene_labels=["industrial_workspace"]),
            episode_row(ids.project_a, ids.editor_a, scene_labels=["industrial_workspace"]),
        ],
    )
    gaps = _gaps(editor, ids.project_a, dimension_type="SCENE")
    assert gaps["items"], "零反馈项目不应返回空数组"
    scene = _item(gaps, "industrial_workspace")
    assert scene["coverage_source"] == "MEASURED"
    assert scene["current_count"] == 3
    assert scene["open_feedback_count"] == 0
    assert scene["target_count"] is None
    assert scene["suggested_episode_count"] == 0
    assert gaps["freshness"] != "UNKNOWN"
