"""阶段 5 训练来源、Adapter 能力与推理结果引用的领域规则测试。

这些规则负责落地三个此前「有类定义、零构造」的契约错误码：
`model_training_source_invalid`、`model_adapter_unsupported`、
`inference_task_result_invalid`。
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.domain.modelops.adapters import (
    EVALUATE_CAPABILITY,
    INFERENCE_CAPABILITY,
    ensure_capability,
)
from app.domain.modelops.errors import (
    InferenceTaskResultInvalidError,
    ModelAdapterUnsupportedError,
    ModelTrainingSourceInvalidError,
)
from app.domain.modelops.references import validate_result_reference
from app.domain.modelops.training_source import (
    TrainingSourceFacts,
    TrainingSourceRef,
    ensure_training_source,
)

REQUEST_ID = UUID(int=11)
DELIVERY_ID = UUID(int=12)
EXTERNAL_REF_ID = UUID(int=13)


# --------------------------------------------------------------------------- #
# 训练来源：model_training_source_invalid
# --------------------------------------------------------------------------- #
def test_empty_training_source_is_allowed() -> None:
    """训练来源三字段均可空；未声明时不校验。"""
    ensure_training_source(TrainingSourceRef(), None)


def test_missing_training_source_reference_rejected() -> None:
    with pytest.raises(ModelTrainingSourceInvalidError):
        ensure_training_source(TrainingSourceRef(request_id=REQUEST_ID), None)


def test_delivery_must_belong_to_declared_request() -> None:
    facts = TrainingSourceFacts(
        request_id=REQUEST_ID,
        delivery_id=DELIVERY_ID,
        delivery_request_id=UUID(int=99),
    )
    with pytest.raises(ModelTrainingSourceInvalidError):
        ensure_training_source(
            TrainingSourceRef(request_id=REQUEST_ID, delivery_id=DELIVERY_ID), facts
        )


def test_delivery_matching_request_accepted() -> None:
    facts = TrainingSourceFacts(
        request_id=REQUEST_ID,
        delivery_id=DELIVERY_ID,
        delivery_request_id=REQUEST_ID,
    )
    ensure_training_source(TrainingSourceRef(request_id=REQUEST_ID, delivery_id=DELIVERY_ID), facts)


def test_external_ref_must_belong_to_declared_delivery() -> None:
    matching = TrainingSourceFacts(
        request_id=REQUEST_ID,
        delivery_id=DELIVERY_ID,
        delivery_request_id=REQUEST_ID,
        external_ref_id=EXTERNAL_REF_ID,
        external_ref_delivery_id=DELIVERY_ID,
    )
    ensure_training_source(
        TrainingSourceRef(
            request_id=REQUEST_ID, delivery_id=DELIVERY_ID, external_ref_id=EXTERNAL_REF_ID
        ),
        matching,
    )

    mismatched = TrainingSourceFacts(
        request_id=REQUEST_ID,
        delivery_id=DELIVERY_ID,
        delivery_request_id=REQUEST_ID,
        external_ref_id=EXTERNAL_REF_ID,
        external_ref_delivery_id=REQUEST_ID,
    )
    with pytest.raises(ModelTrainingSourceInvalidError):
        ensure_training_source(
            TrainingSourceRef(
                request_id=REQUEST_ID,
                delivery_id=DELIVERY_ID,
                external_ref_id=EXTERNAL_REF_ID,
            ),
            mismatched,
        )


def test_non_real_external_ref_rejected_as_training_source() -> None:
    """MOCK/DRY_RUN 产物不得登记为真实模型版本（N7-007）。"""
    facts = TrainingSourceFacts(
        external_ref_id=EXTERNAL_REF_ID,
        external_ref_delivery_id=DELIVERY_ID,
        external_ref_non_real=True,
    )
    with pytest.raises(ModelTrainingSourceInvalidError):
        ensure_training_source(
            TrainingSourceRef(delivery_id=DELIVERY_ID, external_ref_id=EXTERNAL_REF_ID),
            facts,
        )


def test_real_external_ref_accepted_as_training_source() -> None:
    facts = TrainingSourceFacts(
        external_ref_id=EXTERNAL_REF_ID,
        external_ref_delivery_id=DELIVERY_ID,
        external_ref_non_real=False,
    )
    ensure_training_source(
        TrainingSourceRef(delivery_id=DELIVERY_ID, external_ref_id=EXTERNAL_REF_ID), facts
    )


# --------------------------------------------------------------------------- #
# Adapter 能力：model_adapter_unsupported
# --------------------------------------------------------------------------- #
def test_adapter_without_required_capability_rejected() -> None:
    with pytest.raises(ModelAdapterUnsupportedError):
        ensure_capability({INFERENCE_CAPABILITY}, "submit_evaluation")


def test_adapter_with_required_capability_accepted() -> None:
    ensure_capability({EVALUATE_CAPABILITY}, "submit_evaluation")
    ensure_capability({INFERENCE_CAPABILITY}, "submit_inference")


def test_adapter_without_any_declaration_falls_through_to_503() -> None:
    """空能力声明 = Adapter 未配置（503 `model_adapter_unavailable` 语义），不判 422。"""
    ensure_capability(frozenset(), "submit_evaluation")


def test_unknown_action_is_programming_error() -> None:
    with pytest.raises(KeyError):
        ensure_capability({EVALUATE_CAPABILITY}, "submit_unknown")


# --------------------------------------------------------------------------- #
# 推理结果引用：inference_task_result_invalid
# --------------------------------------------------------------------------- #
def test_result_reference_requires_locatable_object() -> None:
    with pytest.raises(InferenceTaskResultInvalidError):
        validate_result_reference(None)
    with pytest.raises(InferenceTaskResultInvalidError):
        validate_result_reference({})


def test_result_reference_validates_sha256_format() -> None:
    assert validate_result_reference({"object_key": "inference/result.json"})["object_key"] == (
        "inference/result.json"
    )
    digest = "sha256:" + "a" * 64
    assert validate_result_reference({"sha256": digest})["sha256"] == digest
    with pytest.raises(InferenceTaskResultInvalidError):
        validate_result_reference({"sha256": "sha256:not-hex"})


def test_result_reference_rejects_empty_object_key() -> None:
    with pytest.raises(InferenceTaskResultInvalidError):
        validate_result_reference({"object_key": "   "})
