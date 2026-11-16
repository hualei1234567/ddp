"""业务指标取数：按状态统计接入任务与 Episode。

只做 ``GROUP BY`` 聚合，不返回任何业务实体；调用方是 ``/metrics`` 的采集器
（见 ``app.core.metrics`` 关于 pull 采集的决策说明）。

- **不加项目过滤**：指标是运维视角的全局分布，不面向某个项目，也不泄露项目内容；
  标签只有 ``status``（枚举值，基数固定），不会因项目数增长而膨胀。
- **未知状态不容错**：若库里出现领域枚举之外的状态值（只能经由绕过应用的直接改库
  产生），该值不进入样本，但会记 warning。不默认归入某个既有状态——那会伪造数据。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.metrics import EPISODES_GAUGE, INGESTION_JOBS_GAUGE, business_gauge_specs
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.jobs import IngestionJobModel

logger = logging.getLogger("ddp.metrics")

# 参与采集的模型；两者都有枚举化约束的 ``status`` 列。
_StatusedModel = type[EpisodeModel] | type[IngestionJobModel]


def _distribution(
    session: Session,
    model: _StatusedModel,
    metric: str,
    known_statuses: frozenset[str],
) -> dict[str, int]:
    rows = session.execute(
        select(model.status, func.count()).group_by(model.status),
    ).all()
    counts: dict[str, int] = {}
    for raw_status, count in rows:
        status = str(raw_status)
        if status not in known_statuses:
            logger.warning("指标 %s 发现领域枚举之外的状态值，已忽略：%r", metric, status)
            continue
        counts[status] = int(count)
    return counts


def collect_business_counts(
    session_factory: sessionmaker[Session],
) -> Mapping[str, Mapping[str, int]]:
    """采集时查库，返回 ``{指标名: {状态值: 数量}}``。

    会话只读使用（不提交任何事务），用完即关；异常向上抛给
    ``BusinessStateCollector``，由它决定"本次不输出样本"。
    """
    specs = {spec.name: frozenset(spec.statuses) for spec in business_gauge_specs()}
    session = session_factory()
    try:
        return {
            INGESTION_JOBS_GAUGE: _distribution(
                session,
                IngestionJobModel,
                INGESTION_JOBS_GAUGE,
                specs[INGESTION_JOBS_GAUGE],
            ),
            EPISODES_GAUGE: _distribution(
                session,
                EpisodeModel,
                EPISODES_GAUGE,
                specs[EPISODES_GAUGE],
            ),
        }
    finally:
        session.close()
