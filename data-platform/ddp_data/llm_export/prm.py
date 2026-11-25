"""PRM Step-Reward JSONL 导出（V52-017，蓝图 N1 步骤级过程奖励 + N4 格式转换）。

**蓝图要求**（N1「步骤级过程奖励标注」）：允许标注员在 Agent 轨迹的任意
Step 上标记 ``正确 / 冗余绕路 / 幻觉工具调用 / 致命错误``，训练侧需要
Step-level reward JSONL。

**格式契约**（``ddp.prm-step-reward/1.0.0``）：
每行一条轨迹：
``{"task_goal", "trajectory_id", "steps":[{"index","action","reward","label"}...],
 "final_answer_correct", "provenance"}``
- ``reward`` 映射：正确=+1 / 冗余绕路=-0.5 / 幻觉工具调用=-1 / 致命错误=-2；
- 步骤 index 必须连续 0..n-1（与 AGENT_TRACE 载体一致）；
- 资格门禁与 SFT/DPO 相同（未审核/质检不合格整条剔除）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from . import (
    FORMAT_VERSION,
    GENERATOR_VERSION,
    EligibilityReport,
    LlmExportError,
    Provenance,
    partition_by_eligibility,
)
from .writers import _build_manifest, _dump_line

PRM_FORMAT = "PRM_STEP_REWARD_JSONL"

#: 步骤级标签 → reward 映射（蓝图 N1 四分类）
REWARD_BY_LABEL: dict[str, float] = {
    "CORRECT": 1.0,
    "REDUNDANT": -0.5,
    "HALLUCINATED_TOOL": -1.0,
    "FATAL": -2.0,
}
_STEP_LABELS = frozenset(REWARD_BY_LABEL)
_MAX_STEPS = 512


class PrmExportError(LlmExportError):
    """PRM 导出领域错误。"""


class StepLabel(StrEnum):
    CORRECT = "CORRECT"
    REDUNDANT = "REDUNDANT"
    HALLUCINATED_TOOL = "HALLUCINATED_TOOL"
    FATAL = "FATAL"


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    """一个可标注步骤。"""

    index: int
    action: str
    label: StepLabel | None = None
    tool: str | None = None
    observation_digest: str | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise PrmExportError("步骤 index 不能为负")
        if not self.action.strip():
            raise PrmExportError("action 不能为空")

    @property
    def reward(self) -> float:
        if self.label is None:
            raise PrmExportError(f"步骤 {self.index} 未标注，无 reward")
        return REWARD_BY_LABEL[self.label.value]


@dataclass(frozen=True, slots=True)
class TrajectoryRecord:
    """一条完整 Agent 轨迹 + 步级标注结果。"""

    provenance: Provenance
    trajectory_id: str
    task_goal: str
    steps: tuple[TrajectoryStep, ...]
    final_answer_correct: bool | None = None

    def __post_init__(self) -> None:
        if not self.trajectory_id.strip() or not self.task_goal.strip():
            raise PrmExportError("trajectory_id/task_goal 不能为空")
        if not self.steps:
            raise PrmExportError("轨迹至少需要 1 个步骤")
        if len(self.steps) > _MAX_STEPS:
            raise PrmExportError(f"步骤数超上限 {_MAX_STEPS}")
        indexes = [s.index for s in self.steps]
        if indexes != list(range(len(self.steps))):
            raise PrmExportError("步骤 index 必须连续 0..n-1")

    @classmethod
    def from_raw(
        cls,
        provenance: Provenance,
        *,
        trajectory_id: str,
        task_goal: str,
        steps: Sequence[Mapping[str, object]],
        final_answer_correct: bool | None = None,
    ) -> "TrajectoryRecord":
        parsed: list[TrajectoryStep] = []
        for i, raw in enumerate(steps):
            label_raw = raw.get("label")
            label = StepLabel(str(label_raw)) if label_raw else None
            parsed.append(
                TrajectoryStep(
                    index=int(raw.get("index", i)),
                    action=str(raw["action"]),
                    label=label,
                    tool=str(raw["tool"]) if raw.get("tool") else None,
                    observation_digest=(
                        str(raw["observation_digest"])
                        if raw.get("observation_digest")
                        else None
                    ),
                )
            )
        return cls(
            provenance=provenance,
            trajectory_id=trajectory_id,
            task_goal=task_goal,
            steps=tuple(parsed),
            final_answer_correct=final_answer_correct,
        )

    # ── 汇总统计（看板/验收用） ──────────────────────────

    @property
    def labeled_steps(self) -> int:
        return sum(1 for s in self.steps if s.label is not None)

    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.steps:
            if s.label is not None:
                counts[s.label.value] = counts.get(s.label.value, 0) + 1
        return counts

    def total_reward(self) -> float | None:
        if self.labeled_steps == 0:
            return None
        return round(sum(s.reward for s in self.steps if s.label is not None), 6)


def _trajectory_payload(record: TrajectoryRecord) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for s in record.steps:
        if s.label is None:
            # 未标注步骤不进训练文件（红线：未标注 ≠ 正确）
            continue
        steps.append(
            {
                "index": s.index,
                "action": s.action,
                "tool": s.tool,
                "label": s.label.value,
                "reward": s.reward,
            }
        )
    return {
        "trajectory_id": record.trajectory_id,
        "task_goal": record.task_goal,
        "final_answer_correct": record.final_answer_correct,
        "steps": steps,
    }


def export_prm(
    records: Sequence[TrajectoryRecord],
) -> "PrmExportBundle":
    """导出 PRM_STEP_REWARD_JSONL（含资格门禁与 step 级 manifest 统计）。"""
    if not records:
        raise PrmExportError("PRM 导出至少需要 1 条轨迹")
    seen_ids: set[str] = set()
    for r in records:
        if r.trajectory_id in seen_ids:
            raise PrmExportError(f"trajectory_id 重复: {r.trajectory_id}")
        seen_ids.add(r.trajectory_id)

    report: EligibilityReport = partition_by_eligibility(records)  # type: ignore[arg-type]
    eligible_ids = {id(p) for p in report.eligible}
    kept = [r for r in records if id(r.provenance) in eligible_ids]
    if not kept:
        raise PrmExportError("全部记录被门禁剔除（无可导出数据）")

    main_lines: list[bytes] = []
    prov_lines: list[bytes] = []
    for idx, record in enumerate(kept):
        payload = _trajectory_payload(record)
        main_lines.append(_dump_line(payload))
        prov_doc = {
            "row": idx,
            "dataset_version_id": record.provenance.dataset_version_id,
            "sample_id": record.provenance.sample_id,
            "submission_id": record.provenance.submission_id,
            "review_status": record.provenance.review_status.value,
            "qc_status": record.provenance.qc_status.value,
            "schema_version": record.provenance.schema_version,
            "trajectory_id": record.trajectory_id,
            "labeled_steps": record.labeled_steps,
            "label_counts": record.label_counts(),
            "total_reward": record.total_reward(),
        }
        prov_lines.append(_dump_line(prov_doc))

    files = {
        "prm_step_reward.jsonl": b"".join(main_lines),
        "provenance.jsonl": b"".join(prov_lines),
    }
    manifest = _build_manifest(PRM_FORMAT, files, len(kept), report)
    manifest["reward_mapping"] = dict(REWARD_BY_LABEL)
    manifest["label_counts_total"] = _sum_label_counts(kept)
    return PrmExportBundle(
        files=files,
        manifest=manifest,
        format=PRM_FORMAT,
        format_version=FORMAT_VERSION,
        generator_version=GENERATOR_VERSION,
    )


def _sum_label_counts(kept: list[TrajectoryRecord]) -> dict[str, int]:
    total: dict[str, int] = {}
    for r in kept:
        for label, count in r.label_counts().items():
            total[label] = total.get(label, 0) + count
    return dict(sorted(total.items()))


@dataclass(frozen=True, slots=True)
class PrmExportBundle:
    """PRM 导出包（files/manifest 复用 ExportBundle 结构 + 格式标识）。"""

    files: dict[str, bytes]
    manifest: dict[str, Any]
    format: str = PRM_FORMAT
    format_version: str = FORMAT_VERSION
    generator_version: str = GENERATOR_VERSION


def parse_prm_bundle(bundle: PrmExportBundle) -> list[dict[str, Any]]:
    """回读 PRM 导出并校验：reward 与 label 映射一致、步骤 index 连续。"""
    import json

    docs: list[dict[str, Any]] = []
    raw = bundle.files["prm_step_reward.jsonl"].decode("utf-8")
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise PrmExportError(f"第 {lineno} 行为空")
        doc = json.loads(line)
        if set(doc) != {"trajectory_id", "task_goal", "final_answer_correct", "steps"}:
            raise PrmExportError(f"第 {lineno} 行键集合不符: {sorted(doc)}")
        steps = doc["steps"]
        if not isinstance(steps, list) or not steps:
            raise PrmExportError(f"第 {lineno} 行 steps 为空")
        indexes = [s["index"] for s in steps]
        if indexes != sorted(indexes) or any(
            indexes[i + 1] - indexes[i] < 1 for i in range(len(indexes) - 1)
        ):
            raise PrmExportError(f"第 {lineno} 行步骤 index 不连续递增")
        for s in steps:
            if s["label"] not in _STEP_LABELS:
                raise PrmExportError(f"第 {lineno} 行非法标签 {s['label']!r}")
            if s["reward"] != REWARD_BY_LABEL[s["label"]]:
                raise PrmExportError(f"第 {lineno} 行 reward 与 label 不符")
        docs.append(doc)
    return docs


__all__ = [
    "PRM_FORMAT",
    "PrmExportBundle",
    "PrmExportError",
    "REWARD_BY_LABEL",
    "StepLabel",
    "TrajectoryRecord",
    "TrajectoryStep",
    "export_prm",
    "parse_prm_bundle",
]
