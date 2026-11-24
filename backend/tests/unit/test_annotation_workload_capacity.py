"""分配容量预览的领域规则（FR-020）。

为什么这些用例落在 domain 层：预览与硬阻断的**判定规则只有一处**
（``CapacityPolicy.exceeded``），而这条规则要处理的边界——"未配置上限"
与"配了 0"、"正好等于上限"与"超过上限"——与 HTTP、数据库无关。放到
API 测试里靠构造请求来覆盖，既绕不过 Settings 的类型约束（上限不允许
配成 null），也会让边界语义变得难读。

这里锁定的语义：

1. ``None``（未配置）不参与判定，也不产生比率 —— 不是 0；
2. 严格大于才超容量，正好等于上限是 ``AT_CAPACITY``（不阻断）；
3. 预览的 ``will_exceed`` 与 ``CapacityPolicy.exceeded`` 同源，
   因此与"提交会不会被 409 拒绝"必然一致。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation import WorkloadStatus
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.workload import AssignmentCapacityPreview, CapacityPolicy

USER_ID = uuid4()


def _preview(
    *,
    open_items: int = 0,
    open_minutes: int = 0,
    planned_items: int = 0,
    planned_minutes: int = 0,
    max_items: int | None = None,
    max_minutes: int | None = None,
) -> AssignmentCapacityPreview:
    return AssignmentCapacityPreview(
        assignee_id=USER_ID,
        capacity=CapacityPolicy(
            max_open_items=max_items,
            max_planned_minutes=max_minutes,
        ),
        open_item_count=open_items,
        open_planned_minutes=open_minutes,
        planned_item_count=planned_items,
        planned_minutes=planned_minutes,
    )


class TestUnconfiguredLimits:
    """ "没配上限"不是"容量为 0"。"""

    def test_none_limit_never_breached_and_ratio_is_null(self) -> None:
        preview = _preview(
            open_items=999,
            open_minutes=99_999,
            planned_items=999,
            planned_minutes=99_999,
        )

        assert preview.breached_limits == ()
        assert preview.will_exceed is False
        assert preview.item_capacity_ratio is None
        assert preview.minutes_capacity_ratio is None
        assert preview.status is WorkloadStatus.NORMAL

    def test_zero_limit_is_configured_and_breached(self) -> None:
        """配成 0 是"真实零容量"：任何非零分配都超；比率为 null（除零无意义）。"""
        preview = _preview(planned_items=1, max_items=0)

        assert preview.exceeds_open_items is True
        assert preview.item_capacity_ratio is None
        assert preview.status is WorkloadStatus.OVER_CAPACITY


class TestBoundary:
    """严格大于才超容量；等于上限是 AT_CAPACITY。"""

    def test_exactly_at_limit_is_at_capacity_not_exceeded(self) -> None:
        preview = _preview(open_items=1, planned_items=2, max_items=3)

        assert preview.will_exceed is False
        assert preview.item_capacity_ratio == 1.0
        assert preview.status is WorkloadStatus.AT_CAPACITY

    def test_one_over_limit_is_exceeded(self) -> None:
        preview = _preview(open_items=1, planned_items=3, max_items=3)

        assert preview.exceeds_open_items is True
        assert preview.status is WorkloadStatus.OVER_CAPACITY

    def test_both_limits_reported_independently(self) -> None:
        """两条上限各自判定，不能因为其中一条没超就报整体正常。"""
        preview = _preview(
            open_items=9,
            open_minutes=10,
            planned_items=5,
            planned_minutes=10_000,
            max_items=10,
            max_minutes=100,
        )

        assert preview.exceeds_open_items is True
        assert preview.exceeds_planned_minutes is True
        assert preview.breached_limits == ("max_open_items", "max_planned_minutes")
        assert preview.will_exceed is True

    def test_minutes_limit_alone_can_block(self) -> None:
        preview = _preview(
            open_minutes=90,
            planned_minutes=20,
            max_items=100,
            max_minutes=100,
        )

        assert preview.exceeds_open_items is False
        assert preview.exceeds_planned_minutes is True
        assert preview.status is WorkloadStatus.OVER_CAPACITY


class TestProjection:
    """投影值 = 既有占用量 + 本次分配量。"""

    def test_projection_sums_both_parts(self) -> None:
        preview = _preview(open_items=4, open_minutes=120, planned_items=2, planned_minutes=60)

        assert preview.projected_item_count == 6
        assert preview.projected_planned_minutes == 180

    def test_ratio_uses_projection_over_limit(self) -> None:
        preview = _preview(planned_items=2, max_items=8, planned_minutes=50, max_minutes=200)

        assert preview.item_capacity_ratio == 0.25
        assert preview.minutes_capacity_ratio == 0.25


class TestSameSourceAsHardBlock:
    """预览与硬阻断必须同源：同一组输入的结论要一致。"""

    @pytest.mark.parametrize(
        ("open_items", "planned_items", "limit"),
        [
            (0, 1, 10),
            (9, 1, 10),
            (9, 2, 10),
            (10, 0, 10),
            (10, 1, 10),
        ],
    )
    def test_preview_matches_policy_exceeded(
        self,
        open_items: int,
        planned_items: int,
        limit: int,
    ) -> None:
        policy = CapacityPolicy(max_open_items=limit)
        preview = _preview(open_items=open_items, planned_items=planned_items, max_items=limit)

        assert preview.will_exceed is (
            "max_open_items"
            in policy.exceeded(
                open_item_count=open_items,
                planned_item_count=planned_items,
            )
        )


class TestValidation:
    def test_negative_projection_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            _preview(planned_items=-1)

    def test_assignee_must_be_uuid(self) -> None:
        with pytest.raises(DomainValidationError):
            AssignmentCapacityPreview(assignee_id="not-a-uuid", capacity=CapacityPolicy())  # type: ignore[arg-type]

    def test_capacity_must_be_policy(self) -> None:
        with pytest.raises(DomainValidationError):
            AssignmentCapacityPreview(
                assignee_id=USER_ID,
                capacity={"max_open_items": 1},  # type: ignore[arg-type]
            )
