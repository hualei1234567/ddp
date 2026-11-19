"""模型版本的训练来源校验。

契约 `model_training_source_invalid`(422)：「训练来源引用不存在、跨项目或不匹配」。
PRD §4.2 把训练来源声明为三个可选引用：`training_request_id`、
`training_delivery_id`、`external_ref_id`。

本模块只做「声明值之间是否自相符」的领域判定；「引用在库里是否存在、是否同项目」
由应用层经 `TrainingSourceGate` 查得后以 `TrainingSourceFacts` 传入，领域层不访问
仓储。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.modelops.errors import ModelTrainingSourceInvalidError


@dataclass(frozen=True, slots=True)
class TrainingSourceRef:
    """模型版本创建请求里声明的训练来源。"""

    request_id: UUID | None = None
    delivery_id: UUID | None = None
    external_ref_id: UUID | None = None

    @property
    def is_empty(self) -> bool:
        return self.request_id is None and self.delivery_id is None and self.external_ref_id is None


@dataclass(frozen=True, slots=True)
class TrainingSourceFacts:
    """`TrainingSourceGate` 解析出的同项目事实。

    ``delivery_request_id`` 是交付单归属的训练需求，``external_ref_delivery_id``
    是外部训练引用归属的交付单；它们用于判断三个引用是否指向同一条训练链路。
    """

    request_id: UUID | None = None
    delivery_id: UUID | None = None
    delivery_request_id: UUID | None = None
    external_ref_id: UUID | None = None
    external_ref_delivery_id: UUID | None = None
    # 外部训练引用是否为非真实结果（MOCK / DRY_RUN）。``None`` 表示无法判定
    # （例如引用没有执行方式字段的存量记录），不阻断；``True`` 必须拒绝登记。
    # 判定权威在同项目仓储（读 ``execution_mode`` 与 ``external_status``），
    # 领域层只消费布尔事实，不复制执行方式字符串集合。
    external_ref_non_real: bool | None = None


def ensure_training_source(declared: TrainingSourceRef, facts: TrainingSourceFacts | None) -> None:
    """训练来源必须存在、同项目，且三个引用指向同一条训练链路。

    ``facts is None`` 表示至少一个被引用的对象在项目内不存在（或属于其他项目）——
    两种情况对客户端都是 422 同一码，不泄漏存在性。
    """
    if declared.is_empty:
        return
    if facts is None:
        raise ModelTrainingSourceInvalidError("训练来源引用不存在或不属于当前项目")
    if declared.delivery_id is not None and declared.request_id is not None:
        if facts.delivery_request_id != declared.request_id:
            raise ModelTrainingSourceInvalidError("训练交付不属于所声明的训练需求")
    if declared.external_ref_id is not None:
        if facts.external_ref_non_real is True:
            # 契约 model_training_source_invalid(422)：MOCK/DRY_RUN 产物不得
            # 被登记成真实模型版本，否则模拟结果会沿评测/发布链路被当成真实能力。
            raise ModelTrainingSourceInvalidError(
                "外部训练引用是 MOCK/DRY_RUN 结果，不能登记为真实模型版本"
            )
    if declared.external_ref_id is not None and declared.delivery_id is not None:
        if facts.external_ref_delivery_id != declared.delivery_id:
            raise ModelTrainingSourceInvalidError("外部训练引用不属于所声明的训练交付")
    if (
        declared.external_ref_id is not None
        and declared.delivery_id is None
        and declared.request_id is not None
    ):
        if facts.external_ref_delivery_id is None:
            raise ModelTrainingSourceInvalidError("外部训练引用缺少归属交付单")


__all__ = ["TrainingSourceFacts", "TrainingSourceRef", "ensure_training_source"]
