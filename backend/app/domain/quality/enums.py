"""统一质量引擎的封闭枚举。

六类分层是**契约的一部分**，不是可自由扩展的标签：新增类别意味着
"同一份数据多了一层结论"，必须同步更新 API 聚合、前端展示与历史对比口径。
因此这里用封闭枚举 + 数据库 CHECK，未知值一律拒绝，绝不"当作 OTHER 放过"。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from app.domain.quality.errors import DomainValidationError


class QualityCategory(StrEnum):
    """质量结论的六层分类。

    ``OVERALL`` 是**派生类别**：它由其余五类的结论聚合而来，规则源不得直接
    产出 ``OVERALL`` 结论。把聚合结果混进原始结论会让"哪条规则判了什么"
    无法追溯。
    """

    INTEGRITY = "INTEGRITY"
    TEMPORAL = "TEMPORAL"
    VISUAL = "VISUAL"
    METADATA = "METADATA"
    ANNOTATION = "ANNOTATION"
    OVERALL = "OVERALL"


#: 规则源可以实际产出的类别；``OVERALL`` 不在其中。
RULE_CATEGORIES: tuple[QualityCategory, ...] = (
    QualityCategory.INTEGRITY,
    QualityCategory.TEMPORAL,
    QualityCategory.VISUAL,
    QualityCategory.METADATA,
    QualityCategory.ANNOTATION,
)


class QualitySeverity(StrEnum):
    """规则严重级别：``ERROR`` 阻断，``WARN`` 只提示。

    与标注域 ``QualityRuleSeverity`` 保持同一语义（两级，不设三级），但类型
    独立：跨域直接 import 标注域内部枚举会让质量引擎绑死在标注模块上。
    """

    ERROR = "ERROR"
    WARN = "WARN"


class QualityFindingStatus(StrEnum):
    """单条规则的判定结果。

    ``NOT_APPLICABLE`` 表示**没有结论**（缺上下文、规则不适用于该对象），
    与 ``PASS`` 有本质区别：前者 ``score`` 必须为 ``NULL``，后者为 ``1.0``。
    把"没判"落成"通过"等于伪造质量证据。
    """

    PASS = "PASS"  # noqa: S105 - 枚举值不是凭据
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class QualityCategoryStatus(StrEnum):
    """单类聚合状态（派生值，不落库为 finding 状态）。"""

    PASS = "PASS"  # noqa: S105 - 枚举值不是凭据
    FAIL = "FAIL"
    WARN = "WARN"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class QualityRunStatus(StrEnum):
    """一次质量运行的整体结论。

    ``INCOMPLETE`` 用于"所有规则都没有结论"的场景：此时既不能说通过，
    也不能说失败——把它映射成 ``PASSED`` 会让未检查的数据流入下游。
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"


class QualityRunLifecycle(StrEnum):
    """异步质检的运行状态，与结论（:class:`QualityRunStatus`）**正交**。

    为什么必须分开：``PASSED`` / ``FAILED`` / ``INCOMPLETE`` 都是"已经算完"
    的结论；把它们与 ``QUEUED`` / ``RUNNING`` 混进一个枚举，读取方只能靠
    字符串白名单猜"这一行到底算完了没有"，而任何遗漏都会让"没算完"被当成
    "通过"。分成两个事实后，查询结论的入口可以只认 ``SUCCEEDED``，
    未完成的行永远进不了结论视图。
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


#: 允许的状态迁移。**只允许这些边**，其余一律拒绝：
#: - ``QUEUED`` 只能进入执行或取消，不能直接跳到结论（否则"没跑"= "通过"）；
#: - ``FAILED`` 只能重投，不能凭空成功；
#: - ``SUCCEEDED`` / ``CANCELLED`` 是终态。
QUALITY_RUN_TRANSITIONS: Mapping[QualityRunLifecycle, frozenset[QualityRunLifecycle]] = {
    QualityRunLifecycle.QUEUED: frozenset(
        {QualityRunLifecycle.RUNNING, QualityRunLifecycle.CANCELLED}
    ),
    QualityRunLifecycle.RUNNING: frozenset(
        {
            QualityRunLifecycle.SUCCEEDED,
            QualityRunLifecycle.FAILED,
            QualityRunLifecycle.CANCELLED,
        }
    ),
    QualityRunLifecycle.RETRYING: frozenset(
        {QualityRunLifecycle.RUNNING, QualityRunLifecycle.CANCELLED}
    ),
    QualityRunLifecycle.FAILED: frozenset({QualityRunLifecycle.RETRYING}),
    QualityRunLifecycle.SUCCEEDED: frozenset(),
    QualityRunLifecycle.CANCELLED: frozenset(),
}

#: 终态：不再接受任何迁移。
TERMINAL_LIFECYCLES: frozenset[QualityRunLifecycle] = frozenset(
    {QualityRunLifecycle.SUCCEEDED, QualityRunLifecycle.CANCELLED}
)


def coerce_lifecycle(value: QualityRunLifecycle | str) -> QualityRunLifecycle:
    if isinstance(value, QualityRunLifecycle):
        return value
    try:
        return QualityRunLifecycle(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualityRunLifecycle)
        raise DomainValidationError(f"未知的运行生命周期：{value!r}（允许：{allowed}）") from exc


class QualitySubjectType(StrEnum):
    """质量检查的对象类型。

    ``DATASET_VERSION`` 已注册规则源（数据集质量报告），但本批次不开放
    HTTP 入口；``PUBLIC_SUBJECT_TYPES`` 明确客户端可请求的范围。
    """

    EPISODE = "EPISODE"
    DATASOURCE_IMPORT = "DATASOURCE_IMPORT"
    DATASET_VERSION = "DATASET_VERSION"


#: 本批次 API 接受的对象类型；批量/自动质检留给后续迭代。
PUBLIC_SUBJECT_TYPES: tuple[QualitySubjectType, ...] = (
    QualitySubjectType.EPISODE,
    QualitySubjectType.DATASOURCE_IMPORT,
)


def coerce_category(value: QualityCategory | str) -> QualityCategory:
    """把外部输入转成类别；未知值显式失败（不静默忽略）。"""
    if isinstance(value, QualityCategory):
        return value
    try:
        return QualityCategory(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualityCategory)
        raise DomainValidationError(f"未知的质量类别：{value!r}（允许：{allowed}）") from exc


def coerce_severity(value: QualitySeverity | str) -> QualitySeverity:
    if isinstance(value, QualitySeverity):
        return value
    try:
        return QualitySeverity(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualitySeverity)
        raise DomainValidationError(f"未知的严重级别：{value!r}（允许：{allowed}）") from exc


def coerce_finding_status(value: QualityFindingStatus | str) -> QualityFindingStatus:
    if isinstance(value, QualityFindingStatus):
        return value
    try:
        return QualityFindingStatus(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualityFindingStatus)
        raise DomainValidationError(f"未知的判定状态：{value!r}（允许：{allowed}）") from exc


def coerce_run_status(value: QualityRunStatus | str) -> QualityRunStatus:
    if isinstance(value, QualityRunStatus):
        return value
    try:
        return QualityRunStatus(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualityRunStatus)
        raise DomainValidationError(f"未知的运行状态：{value!r}（允许：{allowed}）") from exc


def coerce_subject_type(value: QualitySubjectType | str) -> QualitySubjectType:
    if isinstance(value, QualitySubjectType):
        return value
    try:
        return QualitySubjectType(value)
    except ValueError as exc:
        allowed = "、".join(item.value for item in QualitySubjectType)
        raise DomainValidationError(f"未知的检查对象类型：{value!r}（允许：{allowed}）") from exc


__all__ = [
    "PUBLIC_SUBJECT_TYPES",
    "QUALITY_RUN_TRANSITIONS",
    "RULE_CATEGORIES",
    "TERMINAL_LIFECYCLES",
    "QualityCategory",
    "QualityCategoryStatus",
    "QualityFindingStatus",
    "QualityRunLifecycle",
    "QualityRunStatus",
    "QualitySeverity",
    "QualitySubjectType",
    "coerce_category",
    "coerce_finding_status",
    "coerce_lifecycle",
    "coerce_run_status",
    "coerce_severity",
    "coerce_subject_type",
]
