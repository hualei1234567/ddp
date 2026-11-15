"""乐观锁前置判定——全仓唯一实现（R-8 收敛）。

## 为什么需要两种入参约定

``ensure_expected_version`` 此前在 4 个 ``_*_sql.py`` 里各写一份，其中数据集域
那份接受「``expected`` 或 ``expected+1``」，其余三份只接受 ``expected``。
表面上像"写法不一致"，实际是**入参约定不同**：

| 域 | 调用方传给仓储的实体 | 正确判定 |
|---|---|---|
| collection / annotation / devices | 实体是**加载时的版本**（未自增） |
  `entity_version == expected` |
| datasets | 实体是**已自增的候选版本**（`update_dataset`
  与 `version_actions` 都自增） | `entity_version in {expected, expected+1}` |

所以不能简单合并成一种判定：改成严格相等会让数据集域全部 409；
改成差一容忍会让其余三域放过"版本未刷新"的调用。

## 真正的收敛点

把**判定逻辑**收成一份，把**入参约定**显式化：调用方通过
``accept_candidate`` 声明自己传的是哪种实体。这样：

- 只有一处实现 ``if`` 判断；
- 差异变成显式参数，而不是散落在四份函数体里靠肉眼对比；
- 新增域必须选一种，不能"顺手复制一份"。

## 安全性

本函数只是前置校验。真正防并发的是 SQL 的 ``WHERE version = expected_version``
与未命中时的 ``raise_update_failure``（它重新读行并按严格相等判定）。
因此两种约定**都不会放行并发写**——差别只在"何时报错"，不在"是否报错"。

## 为什么不依赖具体错误类

每个域有自己的异常类（``CollectionDomainError`` / ``DatasetDomainError`` / …），
契约错误码虽同但类型不同。错误构造由调用方通过工厂传入，避免仓储层跨域耦合。
"""

from __future__ import annotations

from collections.abc import Callable


def ensure_expected_version(
    entity_version: int,
    expected_version: int,
    *,
    invalid: Callable[[str], Exception],
    conflict: Callable[[], Exception],
    accept_candidate: bool = False,
) -> None:
    """前置乐观锁判定。

    - ``expected_version < 1`` → ``invalid("expected_version 必须大于等于 1")``
    - 版本不匹配 → ``conflict()``

    ``accept_candidate=True`` 表示 ``entity_version`` 是已自增的候选版本
    （``expected+1`` 也算匹配）。目前只有 datasets 域使用这种约定。
    """
    if expected_version < 1:
        raise invalid("expected_version 必须大于等于 1")
    if accept_candidate:
        matched = entity_version in {expected_version, expected_version + 1}
    else:
        matched = entity_version == expected_version
    if not matched:
        raise conflict()


__all__ = ["ensure_expected_version"]
