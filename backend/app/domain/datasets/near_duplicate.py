"""近重复检测：按**元数据信号**判断两个 Episode 是否疑似同一段拍摄。

**必须说清楚它不是什么**

真正的"近重复"（同一动作不同角度、同一场景换机位）需要视觉特征向量或
感知哈希，那属于 N3 多模态 Pipeline 的能力，当前 Episode 上没有帧级特征。
本模块做的是**可解释的元数据近似重复**：

- 时间区间重叠度（两个 Episode 覆盖同一段时间）
- 场景标签重合
- 时长接近

三条同时满足才判为近重复。这是**保守的**——宁可漏报也不误杀。误杀会把
真正不同的样本踢出数据集，而漏报只是少了一道保护。

**为什么先做这个而不是等特征向量**

蓝图 AST-003 要求「近重复去重」，而当前 `pipeline.py` 只会产出
``NEAR_DUPLICATE_NOT_IMPLEMENTED`` 警告、``BLOCK`` 模式直接拒绝整个构建。
先用元数据信号把最常见的重复形态（同一段拍摄被切成多个 Episode）挡住，
比什么都不做更接近要求；同时**如实标注能力边界**，不声称做了视觉查重。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.selection import EpisodeCandidate

_NAMESPACE = UUID("9c1f2a44-7d3b-4e58-8a91-0b6c5d4e3f22")

# 默认阈值（可由策略覆盖，禁止硬编码到判定逻辑里）：
# - 时间重叠率 >= 0.8 视为覆盖同一段时间；
# - 时长相对差 <= 0.2 视为同一测量；
# - 场景标签必须完全一致（缺失时退化为"不参与判定"）。
DEFAULT_OVERLAP_RATIO = 0.8
DEFAULT_DURATION_TOLERANCE = 0.2


class NearDuplicateMethod:
    """已实现的方法名；策略里声明其他方法时显式拒绝，不静默降级。"""

    TEMPORAL_OVERLAP = "TEMPORAL_OVERLAP_V1"
    SUPPORTED = frozenset({TEMPORAL_OVERLAP})


@dataclass(frozen=True, slots=True)
class NearDuplicatePair:
    """一对疑似近重复的 Episode 及判定依据。"""

    left_episode_id: UUID
    right_episode_id: UUID
    overlap_ratio: float
    duration_delta_ratio: float
    shared_scene_labels: tuple[str, ...]
    group_id: UUID

    def to_payload(self) -> dict[str, object]:
        return {
            "left_episode_id": str(self.left_episode_id),
            "right_episode_id": str(self.right_episode_id),
            "overlap_ratio": round(self.overlap_ratio, 6),
            "duration_delta_ratio": round(self.duration_delta_ratio, 6),
            "shared_scene_labels": list(self.shared_scene_labels),
            "group_id": str(self.group_id),
        }


@dataclass(frozen=True, slots=True)
class NearDuplicateOutcome:
    pairs: tuple[NearDuplicatePair, ...]
    cluster_count: int
    clusters: tuple[tuple[UUID, ...], ...]

    @property
    def found(self) -> bool:
        return bool(self.pairs)

    def summary(self) -> dict[str, object]:
        return {
            "method": NearDuplicateMethod.TEMPORAL_OVERLAP,
            "pair_count": len(self.pairs),
            "cluster_count": self.cluster_count,
            "clusters": [[str(item) for item in cluster] for cluster in self.clusters],
            "pairs": [pair.to_payload() for pair in self.pairs],
        }


def detect_near_duplicates(
    candidates: tuple[EpisodeCandidate, ...],
    *,
    method: str = NearDuplicateMethod.TEMPORAL_OVERLAP,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    duration_tolerance: float = DEFAULT_DURATION_TOLERANCE,
    min_cluster_size: int = 2,
) -> NearDuplicateOutcome:
    """按元数据信号检测近重复；结果确定性（同输入必同输出）。

    判定条件（三条同时满足）：时间重叠率达标、时长相对差达标、场景标签一致。
    场景标签缺失的候选**不参与判定**——没有依据时不做断言。
    """
    if method not in NearDuplicateMethod.SUPPORTED:
        # 带上 details：契约里该字段是自由字符串，调用方只能从响应知道
        # "哪个值不可用、可用值是什么"。没有它，422 只有一句「校验失败」，
        # 只能靠猜或翻代码。
        raise DomainValidationError(
            f"未实现的近重复方法：{method!r}；已实现：{sorted(NearDuplicateMethod.SUPPORTED)}",
            details={
                "field": "similarity_method",
                "provided": str(method),
                "supported": sorted(NearDuplicateMethod.SUPPORTED),
            },
        )
    if not 0.0 < overlap_ratio <= 1.0:
        raise DomainValidationError("overlap_ratio 必须在 (0, 1] 之间")
    if not 0.0 <= duration_tolerance < 1.0:
        raise DomainValidationError("duration_tolerance 必须在 [0, 1) 之间")
    if min_cluster_size < 2:
        raise DomainValidationError("min_cluster_size 不得小于 2")

    usable = [item for item in candidates if item.scene_labels]
    # 按 id 排序保证结果与输入顺序无关。
    usable.sort(key=lambda item: str(item.episode_id))

    pairs: list[NearDuplicatePair] = []
    adjacency: dict[UUID, set[UUID]] = {}
    for index, left in enumerate(usable):
        for right in usable[index + 1 :]:
            verdict = _compare(
                left,
                right,
                overlap_ratio=overlap_ratio,
                duration_tolerance=duration_tolerance,
            )
            if verdict is None:
                continue
            shared, overlap, delta = verdict
            group_id = uuid5(
                _NAMESPACE,
                f"{min(str(left.episode_id), str(right.episode_id))}|"
                f"{max(str(left.episode_id), str(right.episode_id))}",
            )
            pairs.append(
                NearDuplicatePair(
                    left_episode_id=left.episode_id,
                    right_episode_id=right.episode_id,
                    overlap_ratio=overlap,
                    duration_delta_ratio=delta,
                    shared_scene_labels=shared,
                    group_id=group_id,
                )
            )
            adjacency.setdefault(left.episode_id, set()).add(right.episode_id)
            adjacency.setdefault(right.episode_id, set()).add(left.episode_id)

    clusters = _clusters(adjacency, min_cluster_size=min_cluster_size)
    return NearDuplicateOutcome(
        pairs=tuple(pairs),
        cluster_count=len(clusters),
        clusters=tuple(clusters),
    )


def _compare(
    left: EpisodeCandidate,
    right: EpisodeCandidate,
    *,
    overlap_ratio: float,
    duration_tolerance: float,
) -> tuple[tuple[str, ...], float, float] | None:
    if left.episode_lineage_id == right.episode_lineage_id:
        # 同一血缘是精确重复，不是近重复；交给 exact dedup 处理。
        return None
    shared = tuple(sorted(set(left.scene_labels) & set(right.scene_labels)))
    if not shared:
        return None
    overlap = _overlap_ratio(left, right)
    if overlap < overlap_ratio:
        return None
    delta = _duration_delta_ratio(left, right)
    if delta > duration_tolerance:
        return None
    return shared, overlap, delta


def _overlap_ratio(left: EpisodeCandidate, right: EpisodeCandidate) -> float:
    start = max(left.start_time_ns, right.start_time_ns)
    end = min(left.end_time_ns, right.end_time_ns)
    if end <= start:
        return 0.0
    intersection = end - start
    shorter = min(left.duration_ns, right.duration_ns)
    if shorter <= 0:
        return 0.0
    return min(1.0, intersection / shorter)


def _duration_delta_ratio(left: EpisodeCandidate, right: EpisodeCandidate) -> float:
    longer = max(left.duration_ns, right.duration_ns)
    if longer <= 0:
        return 1.0
    return abs(left.duration_ns - right.duration_ns) / longer


def _clusters(
    adjacency: dict[UUID, set[UUID]],
    *,
    min_cluster_size: int,
) -> list[tuple[UUID, ...]]:
    """连通分量；只保留规模达标的簇，顺序确定。"""
    seen: set[UUID] = set()
    clusters: list[tuple[UUID, ...]] = []
    for node in sorted(adjacency, key=str):
        if node in seen:
            continue
        stack = [node]
        component: list[UUID] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency.get(current, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        if len(component) >= min_cluster_size:
            clusters.append(tuple(sorted(component, key=str)))
    clusters.sort(key=lambda item: (len(item), str(item[0])))
    return clusters


__all__ = [
    "DEFAULT_DURATION_TOLERANCE",
    "DEFAULT_OVERLAP_RATIO",
    "NearDuplicateMethod",
    "NearDuplicateOutcome",
    "NearDuplicatePair",
    "detect_near_duplicates",
]
