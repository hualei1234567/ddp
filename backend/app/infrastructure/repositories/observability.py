"""业务指标取数：状态分布 + 队列积压 + 导入吞吐 + 存储完整性。

只做聚合查询，不返回任何业务实体；调用方是 ``/metrics`` 的采集器
（见 ``app.core.metrics`` 关于 pull 采集与标签基数的决策说明）。

## 口径

- **状态分布**（``ddp_ingestion_jobs`` / ``ddp_episodes`` /
  ``ddp_data_source_imports`` / ``ddp_processing_runs`` / ``ddp_quality_runs``）：
  全库按 ``status`` 计数。不加项目过滤：指标是运维视角的全局分布，不面向某个
  项目；标签只有 ``status``（枚举值，基数固定），不含 ``project_id``。
- **outbox 积压**（``ddp_outbox_due_events`` / ``..._oldest_due_age_seconds`` /
  ``..._failed_events``）：只统计已到投递时间（``available_at <= now``）的
  ``PENDING`` 与死信 ``FAILED``。前两者决定"现在有没有活干、积压了多久"，
  不含尚未到期的重试事件。
- **导入吞吐**（``ddp_data_source_import_last_success_timestamp_seconds`` /
  ``ddp_data_source_imports_succeeded_24h``）：只统计 ``SUCCEEDED`` 的
  ``finished_at``；24h 是滑动窗口，窗口是口径的一部分，写进指标 help。
- **存储缺失**（``ddp_raw_assets_object_missing``）：``QUARANTINED`` 且
  ``quarantine_codes`` 含 ``object_missing`` 的原始资产。这是数据库里唯一的
  持久证据——对象内容缺失只会在校验时被发现并落成隔离原因，不另建清单。
- **未知状态不容错**：库里出现领域枚举之外的状态值时，该值不进入样本但记
  warning。不默认归入某个既有状态——那会伪造数据。

## 成本与失败隔离

- 每个表一次 ``GROUP BY``（导入把状态分布、最近成功时间与 24h 计数合并到
  同一次扫描；outbox 三个指标走 ``(status, available_at)`` 索引范围）。
- PostgreSQL 上每个查询包在只读短事务里并设置 ``SET LOCAL statement_timeout``；
  超时/失败**只影响该指标组**：该组本次不输出样本（不输出假 0），其余组继续。
- **已知债务（TD-11）**：状态列没有以 ``status`` 为前缀的全局索引，
  ``imports`` / ``processing_runs`` / ``quality_runs`` 的聚合在数据量上来后
  需要预聚合或补索引；当前不新增迁移，先用超时兜底。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol, TypeVar

from sqlalchemy import and_, case, cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from app.core.metrics import (
    EPISODES_GAUGE,
    IMPORT_LAST_SUCCESS_GAUGE,
    IMPORT_SUCCEEDED_24H_GAUGE,
    IMPORTS_GAUGE,
    INGESTION_JOBS_GAUGE,
    OUTBOX_DUE_GAUGE,
    OUTBOX_FAILED_GAUGE,
    OUTBOX_OLDEST_DUE_AGE_GAUGE,
    PROCESSING_RUNS_GAUGE,
    QUALITY_RUNS_GAUGE,
    RAW_ASSETS_OBJECT_MISSING_GAUGE,
    BusinessGaugeSpec,
    BusinessSnapshot,
    business_gauge_specs,
)
from app.domain.datasources.enums import ImportStatus
from app.domain.ingestion.enums import RawAssetQuarantineCode, RawAssetStatus
from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.db.models.datasources import DataSourceImportModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.db.models.processing import ProcessingRunModel
from app.infrastructure.db.models.quality import QualityRunModel
from app.infrastructure.repositories._json_array_predicates import (
    json_array_any_of,
    session_dialect,
)
from app.infrastructure.repositories._sql_support import as_utc

logger = logging.getLogger("ddp.metrics")

# 单条业务指标查询的硬上限：抓取默认 15s 一次，单查询超过 2s 说明数据量已超出
# 当前 pull 方案的上限（见 TD-11），应改为预聚合，而不是让 /metrics 拖垮数据库。
BUSINESS_QUERY_TIMEOUT_MS = 2000

# 导入吞吐的滑动窗口；改动会改变仪表盘语义，因此 metric help 里也写明。
IMPORT_THROUGHPUT_WINDOW = timedelta(hours=24)

# outbox 状态取值与 ``db/models/collection.py`` 的 CHECK 约束、
# ``application/audit.py`` 的语义一一对应。领域层没有对应枚举，沿用既有仓储
# （``outbox_validation.py``）的局部常量做法，避免在指标里另造字符串。
_OUTBOX_PENDING = "PENDING"
_OUTBOX_FAILED = "FAILED"

_T = TypeVar("_T")


class _StatusedModel(Protocol):
    """参与状态分布聚合的 ORM 模型：只需要一个 ``status`` 列。"""

    status: InstrumentedAttribute[str]


@contextmanager
def _read_transaction(session: Session) -> Iterator[None]:
    """短只读事务；PostgreSQL 上限制单条查询耗时，失败由调用方隔离。"""
    with session.begin():
        if session.get_bind().dialect.name == "postgresql":
            # 常量整数拼接，不接收外部输入；SET LOCAL 随事务结束自动复位，
            # 不会把超时设置泄漏到连接池里的其他使用者。
            session.execute(text(f"SET LOCAL statement_timeout = {BUSINESS_QUERY_TIMEOUT_MS}"))
        yield


def _guard(metric: str, query: Callable[[], _T]) -> _T | None:
    """执行一组指标查询；失败时只丢这一组并留日志，不影响其他指标与 /metrics。"""
    try:
        return query()
    except Exception:
        logger.warning("业务指标 %s 采集失败，本次不输出该指标样本", metric, exc_info=True)
        return None


def _distribution(
    session: Session,
    model: type[_StatusedModel],
    metric: str,
    known_statuses: frozenset[str],
) -> dict[str, int]:
    with _read_transaction(session):
        rows = session.execute(
            # 质量运行的生命周期与结论正交：未完成的运行没有 ``status``，
            # 不计入"按结论的运行数"，也不该在每次抓取时打一条警告。
            select(model.status, func.count())
            .where(model.status.is_not(None))
            .group_by(model.status),
        ).all()
    counts: dict[str, int] = {}
    for raw_status, count in rows:
        status = str(raw_status)
        if status not in known_statuses:
            logger.warning("指标 %s 发现领域枚举之外的状态值，已忽略：%r", metric, status)
            continue
        counts[status] = int(count)
    return counts


def _import_metrics(
    session: Session,
    now: datetime,
    known_statuses: frozenset[str],
) -> tuple[dict[str, int], datetime | None, int]:
    """一次扫描同时算出导入状态分布、最近成功时间与 24h 成功数。

    三个口径都只依赖 ``data_source_imports.status/finished_at``，拆成三条查询
    会让抓取成本翻倍。条件聚合用 ``FILTER`` 的等价写法（``CASE``），SQLite 与
    PostgreSQL 都支持。窗口边界取采集进程的 UTC 时钟（与 Worker 的 ``now``
    口径一致），不依赖数据库时区设置。
    """
    succeeded = ImportStatus.SUCCEEDED.value
    window_start = now - IMPORT_THROUGHPUT_WINDOW
    succeeded_finished_at = case(
        (
            DataSourceImportModel.status == succeeded,
            DataSourceImportModel.finished_at,
        )
    )
    succeeded_recent = case(
        (
            and_(
                DataSourceImportModel.status == succeeded,
                DataSourceImportModel.finished_at >= window_start,
            ),
            1,
        ),
        else_=0,
    )
    with _read_transaction(session):
        rows = session.execute(
            select(
                DataSourceImportModel.status,
                func.count(),
                func.max(succeeded_finished_at),
                func.sum(succeeded_recent),
            ).group_by(DataSourceImportModel.status)
        ).all()

    counts: dict[str, int] = {}
    last_success_at: datetime | None = None
    succeeded_24h = 0
    for raw_status, count, last_finished, recent in rows:
        status = str(raw_status)
        if status not in known_statuses:
            logger.warning("指标 %s 发现领域枚举之外的状态值，已忽略：%r", IMPORTS_GAUGE, status)
            continue
        counts[status] = int(count)
        if status == succeeded:
            last_success_at = as_utc(last_finished)
            succeeded_24h += int(recent or 0)
    return counts, last_success_at, succeeded_24h


def _outbox_due_count(session: Session, now: datetime) -> int:
    with _read_transaction(session):
        return int(
            session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.status == _OUTBOX_PENDING,
                    OutboxEvent.available_at <= now,
                )
            ).scalar_one()
        )


def _outbox_oldest_due_age_seconds(session: Session, now: datetime) -> float:
    with _read_transaction(session):
        oldest = session.execute(
            select(func.min(OutboxEvent.available_at)).where(
                OutboxEvent.status == _OUTBOX_PENDING,
                OutboxEvent.available_at <= now,
            )
        ).scalar_one()
    normalized = as_utc(oldest)
    if normalized is None:
        return 0.0
    # 时钟偏差可能让"到期"时间略晚于 now；下限取 0，不输出负年龄。
    return max(0.0, (now - normalized).total_seconds())


def _outbox_failed_count(session: Session) -> int:
    with _read_transaction(session):
        return int(
            session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.status == _OUTBOX_FAILED)
            ).scalar_one()
        )


def _object_missing_count(session: Session) -> int:
    dialect = session_dialect(session)
    column = RawAssetModel.quarantine_codes
    if dialect == "postgresql":
        # quarantine_codes 是普通 JSON 列；jsonb_array_elements_text 只接受 JSONB，
        # 这里显式转换，避免依赖赋值级隐式 cast（函数调用不接受 assignment cast）。
        column = cast(column, JSONB)
    with _read_transaction(session):
        return int(
            session.execute(
                select(func.count())
                .select_from(RawAssetModel)
                .where(
                    RawAssetModel.status == RawAssetStatus.QUARANTINED.value,
                    json_array_any_of(
                        column,
                        (RawAssetQuarantineCode.OBJECT_MISSING.value,),
                        dialect=dialect,
                    ),
                )
            ).scalar_one()
        )


# 参与状态分布采集的模型；``IMPORTS_GAUGE`` 因与吞吐/最近成功合并查询而单列。
_DISTRIBUTION_MODELS: Mapping[str, type[_StatusedModel]] = {
    INGESTION_JOBS_GAUGE: IngestionJobModel,
    EPISODES_GAUGE: EpisodeModel,
    PROCESSING_RUNS_GAUGE: ProcessingRunModel,
    QUALITY_RUNS_GAUGE: QualityRunModel,
}


def _status_distribution_models() -> tuple[tuple[BusinessGaugeSpec, type[_StatusedModel]], ...]:
    specs = tuple(spec for spec in business_gauge_specs() if spec.name in _DISTRIBUTION_MODELS)
    return tuple((spec, _DISTRIBUTION_MODELS[spec.name]) for spec in specs)


def collect_business_snapshot(
    session_factory: sessionmaker[Session],
) -> BusinessSnapshot:
    """采集时查库，返回一次抓取用的业务指标快照。

    会话只读使用（不提交任何事务），用完即关；单组查询失败由 :func:`_guard`
    记录并跳过，异常不向 ``/metrics`` 冒泡。
    """
    now = datetime.now(UTC)
    specs = {spec.name: frozenset(spec.statuses) for spec in business_gauge_specs()}
    session = session_factory()
    try:
        status_counts: dict[str, Mapping[str, int]] = {}
        values: dict[str, float | None] = {}

        imports = _guard(
            IMPORTS_GAUGE,
            partial(_import_metrics, session, now, specs[IMPORTS_GAUGE]),
        )
        if imports is not None:
            import_counts, last_success_at, succeeded_24h = imports
            status_counts[IMPORTS_GAUGE] = import_counts
            values[IMPORT_LAST_SUCCESS_GAUGE] = (
                last_success_at.timestamp() if last_success_at is not None else None
            )
            values[IMPORT_SUCCEEDED_24H_GAUGE] = float(succeeded_24h)

        for spec, model in _status_distribution_models():
            counts = _guard(
                spec.name,
                partial(_distribution, session, model, spec.name, specs[spec.name]),
            )
            if counts is not None:
                status_counts[spec.name] = counts

        def _record(name: str, query: Callable[[], float]) -> None:
            value = _guard(name, query)
            if value is not None:
                values[name] = value

        _record(OUTBOX_DUE_GAUGE, partial(_outbox_due_count, session, now))
        _record(OUTBOX_OLDEST_DUE_AGE_GAUGE, partial(_outbox_oldest_due_age_seconds, session, now))
        _record(OUTBOX_FAILED_GAUGE, partial(_outbox_failed_count, session))
        _record(RAW_ASSETS_OBJECT_MISSING_GAUGE, partial(_object_missing_count, session))

        return BusinessSnapshot(status_counts=status_counts, values=values)
    finally:
        session.close()
