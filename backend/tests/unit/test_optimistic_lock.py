"""R-8：乐观锁前置判定只有一份实现。

锁定的不只是"四种写法合并了"，还包括那个**不能合并掉**的差异：
数据集域接收的是已自增的候选版本，其余域接收加载时的版本。
把两种约定都写成用例，防止有人"简化"成一种而改坏某个域。
"""

from __future__ import annotations

import pytest
from app.domain.collection.errors import (
    DomainValidationError as CollectionInvalid,
)
from app.domain.collection.errors import VersionConflictError as CollectionConflict
from app.infrastructure.repositories._optimistic_lock import ensure_expected_version


def _run(
    entity: int,
    expected: int,
    *,
    accept_candidate: bool = False,
) -> str:
    try:
        ensure_expected_version(
            entity,
            expected,
            invalid=CollectionInvalid,
            conflict=lambda: CollectionConflict("资源"),
            accept_candidate=accept_candidate,
        )
    except CollectionConflict:
        return "conflict"
    except CollectionInvalid:
        return "invalid"
    return "ok"


@pytest.mark.parametrize(("entity", "expected"), [(1, 1), (5, 5), (100, 100)])
def test_strict_convention_accepts_equal_versions(entity: int, expected: int) -> None:
    assert _run(entity, expected) == "ok"


@pytest.mark.parametrize(
    ("entity", "expected"),
    [
        (2, 1),  # 自增后的候选版本，在严格约定下不接受
        (1, 2),
        (5, 4),
        (4, 5),
    ],
)
def test_strict_convention_rejects_mismatch(entity: int, expected: int) -> None:
    assert _run(entity, expected) == "conflict"


def test_strict_convention_rejects_non_positive_expected() -> None:
    assert _run(1, 0) == "invalid"
    assert _run(1, -3) == "invalid"


@pytest.mark.parametrize(("entity", "expected"), [(1, 1), (2, 1)])
def test_candidate_convention_accepts_current_and_incremented(entity: int, expected: int) -> None:
    """数据集域：候选版本（expected+1）必须被接受，否则会误报 409。"""
    assert _run(entity, expected, accept_candidate=True) == "ok"


@pytest.mark.parametrize(("entity", "expected"), [(3, 1), (1, 3), (5, 2)])
def test_candidate_convention_still_rejects_far_mismatch(entity: int, expected: int) -> None:
    assert _run(entity, expected, accept_candidate=True) == "conflict"


def test_all_domain_wrappers_share_the_single_implementation() -> None:
    """四个域的包装函数必须都指向同一份判定，不得再各写一份 if。"""
    import inspect

    from app.infrastructure.repositories import (
        _annotation_sql,
        _collection_sql,
        _dataset_sql,
        _sql_support,
    )

    for module in (_annotation_sql, _collection_sql, _dataset_sql, _sql_support):
        source = inspect.getsource(module.ensure_expected_version)
        assert "_ensure_expected_version(" in source, module.__name__
        # 判定本体不得再出现在包装函数里。
        assert "entity_version ==" not in source, module.__name__
        assert "entity_version in" not in source, module.__name__


def test_domain_wrappers_raise_real_conflict_errors() -> None:
    """真实调用四个域的包装函数，确认它们能构造出各自域的异常。

    上面通过 ``conflict=`` 传入 lambda 的用例抓不到"工厂签名不对"这类错误
    （例如直接传 ``VersionConflictError`` 类，而它需要 ``resource`` 参数）。
    这里走包装函数本身，让每一份的构造路径都被执行。
    """
    from app.infrastructure.repositories import (
        _annotation_sql,
        _collection_sql,
        _dataset_sql,
        _sql_support,
    )

    strict_modules = (_annotation_sql, _collection_sql, _sql_support)
    for module in strict_modules:
        with pytest.raises(Exception) as caught:  # noqa: PT011 —— 各域异常类型不同
            module.ensure_expected_version(2, 1)
        assert type(caught.value).__name__ == "VersionConflictError", module.__name__

    # 数据集域接受候选版本；差得远才报冲突。
    _dataset_sql.ensure_expected_version(2, 1)  # 候选版本：不抛
    with pytest.raises(Exception) as caught:  # noqa: PT011
        _dataset_sql.ensure_expected_version(5, 1)
    assert type(caught.value).__name__ == "VersionConflictError"
