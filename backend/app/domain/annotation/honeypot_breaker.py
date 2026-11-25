"""黄金盲测题熔断器（V52-007，蓝图 N3 Honeypot）。

**蓝图要求**：黄金题以 5% 比例随机混入日常任务流；系统实时计算标注员
黄金题准确率，**连续低于 85% 自动暂停派单并冻结结算**。

**设计**（纯领域，复用既有 gold_set 语义）：
- 滑动窗口统计最近 N 道黄金题的命中；
- 连续 ``consecutive_windows`` 个窗口准确率 < ``pause_threshold`` → 触发 PAUSE；
- PAUSE 可由管理员手动解除（resume），解除后从空窗口重新累计；
- 所有判定确定性可复现，无随机数。

真实接线（派单入口消费 PAUSE 指令）为阶段 2 任务 T5 的前置；本模块先行
落地领域规则并单测钉住，避免蓝图 G2 长期悬空。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

#: 蓝图 N3 数值：黄金题准确率熔断阈值
PAUSE_THRESHOLD = 0.85
#: 最小窗口样本数（防小样本误伤）
MIN_WINDOW = 5
#: 连续触发窗口数
CONSECUTIVE_WINDOWS = 2


class HoneypotError(ValueError):
    """熔断器参数/状态错误。"""


class BreakerState(StrEnum):
    NORMAL = "NORMAL"
    PAUSED = "PAUSED"


@dataclass(frozen=True, slots=True)
class WindowStat:
    """一个窗口的统计。"""

    hits: int
    total: int

    def __post_init__(self) -> None:
        if self.total < 0 or self.hits < 0 or self.hits > self.total:
            raise HoneypotError("hits/total 非法")

    @property
    def accuracy(self) -> float | None:
        return self.hits / self.total if self.total else None

    @property
    def below_threshold(self) -> bool:
        acc = self.accuracy
        return acc is not None and acc < PAUSE_THRESHOLD


@dataclass(frozen=True, slots=True)
class BreakerDecision:
    """一次评估的结论。"""

    state: BreakerState
    #: 触发 PAUSE 时给出可解释依据
    reasons: tuple[str, ...]
    #: 各窗口准确率（None = 样本不足）
    window_accuracies: tuple[float | None, ...]
    #: PAUSED 时冻结结算的建议口径
    freeze_settlement: bool

    @property
    def is_paused(self) -> bool:
        return self.state is BreakerState.PAUSED


class HoneypotBreaker:
    """按标注员维度的黄金题熔断器（无状态输入、显式演进的有限状态机）。"""

    def __init__(
        self,
        *,
        window_size: int = 10,
        pause_threshold: float = PAUSE_THRESHOLD,
        consecutive_windows: int = CONSECUTIVE_WINDOWS,
        min_window: int = MIN_WINDOW,
    ) -> None:
        if window_size < min_window:
            raise HoneypotError("window_size 不能小于 min_window")
        if not 0.0 < pause_threshold <= 1.0:
            raise HoneypotError("pause_threshold 必须在 (0,1]")
        if consecutive_windows < 1:
            raise HoneypotError("consecutive_windows 必须 ≥1")
        self.window_size = window_size
        self.pause_threshold = pause_threshold
        self.consecutive_windows = consecutive_windows
        self.min_window = min_window
        self.state = BreakerState.NORMAL
        self._hits: list[bool] = []
        self._paused_reasons: tuple[str, ...] = ()

    # ── 状态转移 ─────────────────────────────────────────

    def record_gold_result(self, *, hit: bool) -> None:
        """记录一道黄金题的命中结果（标注员无感知）。"""
        if self.state is BreakerState.PAUSED:
            raise HoneypotError("PAUSED 状态下不应再接收新标注（先 resume）")
        self._hits.append(bool(hit))

    def resume(self, *, by: str) -> BreakerDecision:
        """管理员手动解除暂停；清空历史窗口重新累计。"""
        if self.state is not BreakerState.PAUSED:
            raise HoneypotError("未暂停，无需解除")
        self.state = BreakerState.NORMAL
        self._hits = []
        self._paused_reasons = ()
        return BreakerDecision(
            state=BreakerState.NORMAL,
            reasons=(f"由 {by} 手动解除，窗口已重置",),
            window_accuracies=(),
            freeze_settlement=False,
        )

    # ── 评估 ─────────────────────────────────────────────

    def evaluate(self) -> BreakerDecision:
        """用最近 ``window_size * consecutive_windows`` 条记录划分窗口评估。"""
        need = self.window_size * self.consecutive_windows
        recent = self._hits[-need:]
        windows: list[WindowStat] = [
            WindowStat(
                hits=sum(recent[i : i + self.window_size]),
                total=len(recent[i : i + self.window_size]),
            )
            for i in range(0, len(recent), self.window_size)
        ]
        # 只保留满窗口（部分窗口样本不足不参与判定）
        full = [w for w in windows if w.total >= self.window_size]
        accs = tuple(w.accuracy for w in windows) or ()

        if self.state is BreakerState.PAUSED:
            return BreakerDecision(
                state=BreakerState.PAUSED,
                reasons=self._paused_reasons or ("已暂停（等待管理员解除）",),
                window_accuracies=accs,
                freeze_settlement=True,
            )

        below = [w for w in full if w.below_threshold]
        if len(below) >= self.consecutive_windows:
            reasons = [
                f"连续 {len(below)} 个窗口黄金题准确率低于 {self.pause_threshold}："
                + ", ".join(f"{w.hits}/{w.total}={w.accuracy:.0%}" for w in below)
            ]
            self.state = BreakerState.PAUSED
            self._paused_reasons = tuple(reasons)
            return BreakerDecision(
                state=BreakerState.PAUSED,
                reasons=tuple(reasons),
                window_accuracies=accs,
                freeze_settlement=True,
            )

        reasons: list[str] = []
        if not full:
            reasons.append(
                f"样本不足（满窗口 {len(full)}/{self.consecutive_windows}，需 {need} 条），不判定"
            )
        else:
            reasons.append(f"未触发熔断：低准窗口 {len(below)}/{self.consecutive_windows}")
        return BreakerDecision(
            state=BreakerState.NORMAL,
            reasons=tuple(reasons),
            window_accuracies=accs,
            freeze_settlement=False,
        )

    def windows_snapshot(self, results: Sequence[bool]) -> tuple[WindowStat, ...]:
        """纯函数视图：把一段结果按当前窗口大小分窗（供报表/测试）。"""
        return tuple(
            WindowStat(
                hits=sum(results[i : i + self.window_size]),
                total=len(results[i : i + self.window_size]),
            )
            for i in range(0, len(results), self.window_size)
        )


__all__ = [
    "BreakerDecision",
    "BreakerState",
    "CONSECUTIVE_WINDOWS",
    "HoneypotBreaker",
    "HoneypotError",
    "MIN_WINDOW",
    "PAUSE_THRESHOLD",
    "WindowStat",
]
