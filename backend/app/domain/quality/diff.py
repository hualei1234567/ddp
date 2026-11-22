"""质检差异报告（V31-033）：两次运行结论的逐条比对。

**为什么需要它**：批量重跑之后，运维要回答"这次重跑到底改变了什么"——
哪些问题消失了、哪些新出现、哪些还在。只给两个总数（error_count 从 3 降到 1）
无法定位到具体规则，也无法判断重跑是否真的修好了数据。

**对齐键是 ``rule_id + subject_ref``**：``rule_id`` 标识规则，``subject_ref``
区分同一规则作用于多个对象（例如一个 Episode 下的多条标注记录）。只用
``rule_id`` 会把"记录 A 通过、记录 B 失败"错算成"规则状态变化"。

**``NOT_APPLICABLE`` 与 ``PASS`` 必须分开**：前者是"没判"，后者是"判过且通过"。
把两者混为一类会让"规则这次没运行"被读成"规则这次通过了"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.quality.enums import QualityFindingStatus
from app.domain.quality.models import QualityFinding


class DiffKind(StrEnum):
    """单条结论在两次运行之间的变化。"""

    #: 只在候选运行出现——重跑新引入的问题。
    NEW = "NEW"
    #: 只在基线运行出现——重跑修复了的问题。
    RESOLVED = "RESOLVED"
    #: 两次都有且状态相同。
    UNCHANGED = "UNCHANGED"
    #: 两次都有但状态不同（例如 FAIL → PASS）。
    CHANGED = "CHANGED"


@dataclass(frozen=True, slots=True)
class FindingDiff:
    """一条规则在两次运行之间的差异。

    ``baseline`` / ``candidate`` 为 ``None`` 表示该侧没有这条结论；两者都为
    ``None`` 的条目不会出现（差异只描述实际存在的结论）。
    """

    rule_id: str
    subject_ref: str
    kind: DiffKind
    baseline_status: QualityFindingStatus | None
    candidate_status: QualityFindingStatus | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule_id, self.subject_ref)


@dataclass(frozen=True, slots=True)
class QualityRunDiff:
    """两次运行的完整差异。

    ``counts`` 按 :class:`DiffKind` 计数；``findings`` 按
    ``(rule_id, subject_ref)`` 升序，保证同样输入产出同样顺序（便于快照与
    人工核对）。
    """

    baseline_run_id: object
    candidate_run_id: object
    findings: tuple[FindingDiff, ...]

    @property
    def counts(self) -> dict[DiffKind, int]:
        result = {kind: 0 for kind in DiffKind}
        for item in self.findings:
            result[item.kind] += 1
        return result

    @property
    def has_regression(self) -> bool:
        """候选运行是否引入了新问题（``NEW`` 非空）。

        只把 ``NEW`` 算作回归：``CHANGED`` 可能是修好也可能是变坏，需要看
        具体状态；把它算进回归会让"FAIL → PASS"也被判为回归。
        """
        return any(item.kind is DiffKind.NEW for item in self.findings)


def _key(finding: QualityFinding) -> tuple[str, str]:
    return (finding.rule_id, finding.subject_ref)


def _classify(
    baseline: QualityFinding | None,
    candidate: QualityFinding | None,
) -> DiffKind:
    if baseline is None:
        return DiffKind.NEW
    if candidate is None:
        return DiffKind.RESOLVED
    if baseline.status is candidate.status:
        return DiffKind.UNCHANGED
    return DiffKind.CHANGED


def diff_runs(
    *,
    baseline_run_id: object,
    candidate_run_id: object,
    baseline: tuple[QualityFinding, ...],
    candidate: tuple[QualityFinding, ...],
) -> QualityRunDiff:
    """比对两次运行的 findings。

    同一侧出现重复键（同一规则 + 同一对象产出两条结论）视为数据缺陷：
    领域层不猜"该用哪一条"，直接拒绝，让上游修规则实现。
    """
    baseline_by_key: dict[tuple[str, str], QualityFinding] = {}
    for finding in baseline:
        key = _key(finding)
        if key in baseline_by_key:
            raise ValueError(f"基线运行存在重复结论：rule_id={key[0]} subject_ref={key[1]!r}")
        baseline_by_key[key] = finding

    candidate_by_key: dict[tuple[str, str], QualityFinding] = {}
    for finding in candidate:
        key = _key(finding)
        if key in candidate_by_key:
            raise ValueError(f"候选运行存在重复结论：rule_id={key[0]} subject_ref={key[1]!r}")
        candidate_by_key[key] = finding

    diffs: list[FindingDiff] = []
    for key in sorted(set(baseline_by_key) | set(candidate_by_key)):
        base = baseline_by_key.get(key)
        cand = candidate_by_key.get(key)
        diffs.append(
            FindingDiff(
                rule_id=key[0],
                subject_ref=key[1],
                kind=_classify(base, cand),
                baseline_status=base.status if base is not None else None,
                candidate_status=cand.status if cand is not None else None,
            )
        )

    return QualityRunDiff(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        findings=tuple(diffs),
    )


__all__ = ["DiffKind", "FindingDiff", "QualityRunDiff", "diff_runs"]
