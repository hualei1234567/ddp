"""TIMESTAMP_NORMALIZE 阶段处理器（V6.1 视频链）。

## 这个阶段做什么

在 :class:`~app.application.processing.video_decode_stage.VideoDecodeHandler`
写好 ``video_frames`` 的**原始时间**（``pts_seconds`` / ``timestamp_raw`` /
``time_base``）之后，把每帧的**衍生归一化时间**补齐：同一行写
``timestamp_normalized_seconds`` 与 ``normalize_method``，原始列只读不改。

## 红线

- **归一化与原始时间永不混写**：本阶段只写两个衍生列；``pts_seconds`` 等
  原始列是解码器原样带回的事实，永不改写；
- **方法必须显式**：``SECONDS_IDENTITY`` 表示"原始值已是秒、未做任何变换"，
  绝不把"什么都没做"悄悄标成已归一；``RAW_PARSED`` 表示"从原文按时间基换算"；
- **缺原始时间留痕不静默**：``pts_seconds`` 与可解析的 ``timestamp_raw``
  都缺的行记 ``NONE``、归一化值保持 NULL，并在汇总与日志里点名，不静默跳过；
- **时间基错误显式失败**：需要换算却没有合法时间基（缺失/格式非法/分母非正）
  时抛 :class:`StageFailure`（重试无意义，数据不会自己变好），不猜、不兜底
  成 ``NONE``——那会把"时间基坏了"伪装成"没有需要归一化的帧"。

## 为什么单独成模块

与 :mod:`app.application.processing.video_decode_stage` 同构：纯函数
``normalize_frame_timestamp`` 管"怎么算"，处理器 ``TimestampNormalizeHandler``
管"算完落库"。两侧职责分离，归一化规则可脱离数据库单测。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.processing.stages import (
    StageContext,
    StageFailure,
    StageOutcome,
)
from app.domain.processing.enums import ProcessingStepStatus
from app.infrastructure.db.models.video_frame import VideoFrameModel

logger = logging.getLogger("ddp.processing.timestamp_normalize")

#: 归一化方法：原始值已是秒、未做任何变换（显式留痕，绝不静默标成已归一）。
METHOD_SECONDS_IDENTITY = "SECONDS_IDENTITY"
#: 归一化方法：``pts_seconds`` 缺失，从 ``timestamp_raw`` 原文按时间基解析换算。
METHOD_RAW_PARSED = "RAW_PARSED"
#: 归一化方法：两类原始时间都缺，归一化值保持 NULL，留痕不静默跳过。
METHOD_NONE = "NONE"

#: 时间基格式：``num/den``（如 ``1/1000``，即每 tick 一秒的千分之一）。
_TIME_BASE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")

#: 归一化值落库精度：与 ``timestamp_normalized_seconds`` 列的 Numeric(18, 9)
#: 对齐，换算结果统一收敛到 9 位小数，保证同输入重跑值完全一致。
_NORMALIZED_QUANTUM = Decimal("0.000000001")

#: 单次运行待归一化的行数上限（防御异常大的帧索引被整批改写）。
DEFAULT_MAX_FRAME_ROWS = 100_000


class TimestampNormalizeError(ValueError):
    """归一化规则层面的失败（时间基非法等）；由处理器翻译为阶段失败。"""


@dataclass(frozen=True, slots=True)
class TimestampNormalization:
    """一帧的归一化结果：归一化秒数 + 显式方法。"""

    normalized_seconds: Decimal | None
    method: str


def _as_decimal(value: object) -> Decimal:
    """把十进制字符串/数值严格转为 ``Decimal``；无法解析即显式报错。"""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise TimestampNormalizeError(f"数值无法解析：{value!r}") from exc


def _parse_time_base(time_base: str | None) -> Decimal:
    """解析时间基 ``num/den``，返回每 tick 对应的秒数。

    时间基缺失、格式非法或分母非正都视为**坏数据**，显式报错而不是猜一个
    兜底值——按错误时基换算出的"归一化时间"比 NULL 更危险（错得不可见）。
    """
    if time_base is None or not time_base.strip():
        raise TimestampNormalizeError("时间基缺失或为空，无法把原始 pts 换算为秒")
    match = _TIME_BASE_PATTERN.match(time_base)
    if not match:
        raise TimestampNormalizeError(f"时间基格式非法：{time_base!r}（应为 num/den）")
    denominator = Decimal(match.group(2))
    if denominator <= 0:
        raise TimestampNormalizeError(f"时间基分母非正：{time_base!r}")
    return Decimal(match.group(1)) / denominator


def normalize_frame_timestamp(
    pts_seconds: Decimal | float | int | None,
    timestamp_raw: str | None,
    time_base: str | None,
) -> TimestampNormalization:
    """纯函数：给出一帧的原始时间，返回归一化结果（规则唯一权威实现）。

    三类输入对应三个显式方法：

    - ``pts_seconds`` 有值：原始已是秒 → ``SECONDS_IDENTITY``，值原样传递，
      **不做任何变换**（方法名就是"什么都没做"的显式声明）；
    - ``pts_seconds`` 为 NULL 但 ``timestamp_raw`` 可解析：按 ``time_base``
      把原文的 tick 数换算成秒 → ``RAW_PARSED``；
    - 两类原始时间都缺（或 ``timestamp_raw`` 不可解析）：``NONE``，归一化值
      保持 NULL——留痕由调用方（处理器的日志与汇总）承担，这里只如实返回。
    """
    if pts_seconds is not None:
        return TimestampNormalization(
            normalized_seconds=_as_decimal(pts_seconds),
            method=METHOD_SECONDS_IDENTITY,
        )

    if timestamp_raw is not None and timestamp_raw.strip():
        raw = _as_decimal(timestamp_raw)
        seconds_per_tick = _parse_time_base(time_base)
        normalized = (raw * seconds_per_tick).quantize(_NORMALIZED_QUANTUM, rounding=ROUND_HALF_UP)
        return TimestampNormalization(normalized_seconds=normalized, method=METHOD_RAW_PARSED)

    return TimestampNormalization(normalized_seconds=None, method=METHOD_NONE)


class TimestampNormalizeHandler:
    """``TIMESTAMP_NORMALIZE`` 阶段处理器。"""

    def __init__(self, session: Session, *, max_rows: int = DEFAULT_MAX_FRAME_ROWS) -> None:
        self._session = session
        self._max_rows = max_rows

    def __call__(self, context: StageContext) -> StageOutcome:
        rows = self._pending_rows(context)
        if not rows:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=(
                    "跳过：当前运行没有待归一化的帧行" "（未登记视频资产，或帧时间均已归一化）"
                ),
            )
        if len(rows) > self._max_rows:
            raise ValueError(
                f"待归一化帧行数 {len(rows)} 超过上限 {self._max_rows}："
                "拒绝整批改写异常大的帧索引"
            )

        counts: dict[str, int] = {}
        for row in rows:
            try:
                normalization = normalize_frame_timestamp(
                    row.pts_seconds, row.timestamp_raw, row.time_base
                )
            except TimestampNormalizeError as exc:
                raise StageFailure(
                    "timestamp_time_base_invalid",
                    f"帧 {row.asset_id}/{row.stream_id}/frame_index={row.frame_index} "
                    f"时间基非法，拒绝归一化：{exc}",
                    retryable=False,
                ) from exc
            # 只写衍生列：原始时间（pts_seconds 等）是解码器的事实，永不改写。
            row.timestamp_normalized_seconds = normalization.normalized_seconds
            row.normalize_method = normalization.method
            counts[normalization.method] = counts.get(normalization.method, 0) + 1
            if normalization.method == METHOD_NONE:
                # 留痕：缺原始时间的行在日志里点名，汇总里计数，不静默跳过。
                logger.warning(
                    "帧时间缺原始数据，归一化留痕 NONE：asset=%s stream=%s frame_index=%s",
                    row.asset_id,
                    row.stream_id,
                    row.frame_index,
                )
        self._session.flush()

        detail = "、".join(f"{method} {count} 行" for method, count in sorted(counts.items()))
        summary = f"已归一化 {len(rows)} 行帧时间（{detail}）"
        none_count = counts.get(METHOD_NONE, 0)
        if none_count:
            summary += (
                f"；{METHOD_NONE} {none_count} 行缺原始时间，"
                "归一化值保持 NULL（留痕，不静默跳过）"
            )
        return StageOutcome(status=ProcessingStepStatus.SUCCEEDED.value, log_summary=summary)

    def _pending_rows(self, context: StageContext) -> list[VideoFrameModel]:
        """当前运行登记的 ``video_frames`` 行中**尚未归一化**的行。

        - 资产范围取 ``run.input_asset_ids``（与 VIDEO_DECODE 同一来源，
          处理器不自己猜哪些资产属于本次运行）；
        - 只处理 ``timestamp_normalized_seconds IS NULL`` 的行：重跑幂等，
          不重复改写已归一化的行；
        - ``project_id`` 一律取 ``scope.project_id``，防止跨项目改写帧行。
        """
        asset_ids = tuple(getattr(context.run, "input_asset_ids", None) or ())
        if not asset_ids:
            return []
        stmt = (
            select(VideoFrameModel)
            .where(VideoFrameModel.project_id == context.scope.project_id)
            .where(VideoFrameModel.asset_id.in_(asset_ids))
            .where(VideoFrameModel.timestamp_normalized_seconds.is_(None))
        )
        return list(self._session.scalars(stmt))


__all__ = [
    "METHOD_NONE",
    "METHOD_RAW_PARSED",
    "METHOD_SECONDS_IDENTITY",
    "TimestampNormalizeError",
    "TimestampNormalizeHandler",
    "TimestampNormalization",
    "normalize_frame_timestamp",
]
