"""Episode 允许空标定快照：与契约 ``EpisodeCreate.calibration_snapshot`` 对齐。

**为什么这条要单独测**

领域此前强制标定快照非空（抛 ``calibration_not_active``），而契约把它列为
可选、``default: []``。实现比契约更严的后果不是"更安全"，而是：

1. 客户端照契约写就被 422 拒绝——契约失去可信度；
2. Hugging Face 公开第一人称数据集的真实元数据里**根本没有任何标定信息**
   （preview/metadata.jsonl 只有 file_name / example_id / task_name /
   instruction / view / setting / modality / has_wrist / environment /
   episode_seconds / source_fps / excerpt_*），于是这类数据永远建不出
   Episode，N1 的"真实数据接入"到这一步就断了；
3. 数据集侧的 ``CALIBRATION_MISSING`` 排除码被架空成不可达死码。

放宽后，"数据能不能进数据集"仍由数据集策略判定（``CALIBRATION_MISSING`` /
``CALIBRATION_INVALID`` 两个码），门禁没有消失，只是归到了正确的位置。
"""

from __future__ import annotations

import pytest
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.validation import validate_calibration_snapshot


def test_empty_snapshot_is_accepted() -> None:
    """契约默认值：空数组必须通过。"""
    assert validate_calibration_snapshot([]) == ()


def test_valid_item_is_normalized() -> None:
    snapshot = [
        {
            "calibration_id": "11111111-1111-1111-1111-111111111111",
            "calibration_version": 1,
            "checksum": "sha256:" + "a" * 64,
            "target_type": "ROBOT",
            "target_id": "22222222-2222-2222-2222-222222222222",
            "schema_version": "ddp.calibration/1.0.0",
        }
    ]
    result = validate_calibration_snapshot(snapshot)
    assert len(result) == 1
    assert result[0]["target_type"] == "ROBOT"


def test_non_empty_snapshot_is_still_strictly_validated() -> None:
    """放宽只针对"空"；非空时字段校验一点没松。"""
    with pytest.raises(DomainValidationError):
        validate_calibration_snapshot([{"target_type": "ROBOT"}])


def test_unknown_target_type_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        validate_calibration_snapshot(
            [
                {
                    "calibration_id": "11111111-1111-1111-1111-111111111111",
                    "calibration_version": 1,
                    "checksum": "sha256:" + "a" * 64,
                    "target_type": "NOT_A_TARGET",
                    "target_id": "22222222-2222-2222-2222-222222222222",
                    "schema_version": "ddp.calibration/1.0.0",
                }
            ]
        )


def test_non_sequence_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        validate_calibration_snapshot("not-a-list")
