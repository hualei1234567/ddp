"""质检批量重跑与差异报告用例（V31-033）。

职责边界：

- **差异判定**在领域层（``domain.quality.diff.diff_runs``），本模块不写
  "两条结论算不算变化"；
- 本模块只做编排：校验入参、取两次运行的 findings、组装报告。

批量重跑复用 :meth:`QualityRunUseCases.rerun_batch`（同一入队路径），
不另立第二套。

**为什么只比 ``SUCCEEDED`` 运行**：未完成的运行没有可信结论（ADR-0042），
拿它做基线会让"还没算完"被读成"结论变好了"。
"""

from __future__ import annotations

from uuid import UUID

from app.application.quality.ports import QualityRunRepository
from app.domain.identity.models import ProjectScope
from app.domain.quality.diff import QualityRunDiff, diff_runs
from app.domain.quality.enums import QualityRunLifecycle
from app.domain.quality.errors import DomainValidationError, ResourceNotFoundError


class QualityRunDiffUseCases:
    def __init__(self, runs: QualityRunRepository) -> None:
        self._runs = runs

    def compare(
        self,
        scope: ProjectScope,
        baseline_run_id: UUID,
        candidate_run_id: UUID,
    ) -> QualityRunDiff:
        """比对两次运行的结论。

        两次运行必须检查**同一对象**：不同对象的结论差异没有业务含义。
        任一运行未产出结论时拒绝——没有可信结论就没有可比性。
        """
        baseline = self._load_concluded(scope, baseline_run_id)
        candidate = self._load_concluded(scope, candidate_run_id)
        if (
            baseline.subject_type is not candidate.subject_type
            or baseline.subject_id != candidate.subject_id
        ):
            raise DomainValidationError(
                "两次运行必须检查同一对象："
                f"基线 {baseline.subject_type.value}/{baseline.subject_id}，"
                f"候选 {candidate.subject_type.value}/{candidate.subject_id}"
            )
        return diff_runs(
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            baseline=tuple(self._runs.list_findings(scope, baseline_run_id, limit=10_000)),
            candidate=tuple(self._runs.list_findings(scope, candidate_run_id, limit=10_000)),
        )

    def _load_concluded(self, scope: ProjectScope, run_id: UUID):
        run = self._runs.find_any(scope, run_id)
        if run is None:
            raise ResourceNotFoundError("质量运行", run_id)
        if run.lifecycle is not QualityRunLifecycle.SUCCEEDED:
            raise DomainValidationError(
                f"运行 {run_id} 尚未产出结论（lifecycle={run.lifecycle.value}），不可比较"
            )
        return run


__all__ = ["QualityRunDiffUseCases"]
