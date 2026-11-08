"""训练需求/交付/授权/Adapter 回调领域单测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.datasets.adapter_callback import (
    compute_callback_signature,
    verify_callback_signature,
)
from app.domain.datasets.dataset_grants import new_dataset_grant
from app.domain.datasets.errors import (
    DomainValidationError,
    ExportFormatUnsupportedError,
    ExportFormatVersionUnsupportedError,
    InvalidStateTransitionError,
)
from app.domain.datasets.training_deliveries import new_training_delivery
from app.domain.datasets.training_enums import (
    DatasetGrantStatus,
    ExternalTrainingStatus,
    GrantScope,
    RevisionStatus,
    TrainingDeliveryStatus,
    TrainingRequestStatus,
)
from app.domain.datasets.training_errors import (
    DatasetGrantForbiddenError,
    TrainingCallbackInvalidError,
    TrainingDeliveryAcceptanceForbiddenError,
    TrainingRequestApprovalForbiddenError,
    TrainingRequestImmutableError,
    TrainingRequestIncompleteError,
)
from app.domain.datasets.training_references import new_external_reference
from app.domain.datasets.training_requests import (
    new_training_request,
    normalize_requirement_snapshot,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def snapshot(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "business_goal": "训练抓取策略",
        "model_type": "diffusion-policy",
        "training_type": "IMITATION",
        "data_modalities": ["RGB", "PROPRIOCEPTION"],
        "scene_targets": ["kitchen"],
        "action_targets": ["pick"],
        "robot_targets": ["arm-a"],
        "device_targets": ["cam-a"],
        "required_labels": ["action"],
        "required_schema_versions": [],
        "target_episode_count": 100,
        "target_duration_ns": None,
        "quality_thresholds": {"min_quality_score": 0.8},
        "split_requirements": None,
        "leakage_policy_id": None,
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "delivery_due_at": (NOW + timedelta(days=7)).isoformat(),
        "acceptance_method": "离线回放验收",
        "priority": "P1",
    }
    payload.update(overrides)
    return payload


def make_request(**overrides: object):
    actor = uuid4()
    request, revision = new_training_request(
        training_request_id=uuid4(),
        revision_id=uuid4(),
        project_id=uuid4(),
        code=f"TR-{uuid4().hex[:8]}",
        title="训练需求",
        owner_id=uuid4(),
        snapshot=snapshot(**overrides),
        actor_id=actor,
        now=NOW,
    )
    return request, revision, actor


def test_snapshot_validation_and_revision_hash_are_server_side() -> None:
    normalized = normalize_requirement_snapshot(snapshot())
    assert normalized["format_version"] == "ddp.lerobot/1.0.0"
    assert normalized["delivery_due_at"].endswith("+00:00")

    missing = snapshot()
    missing.pop("quality_thresholds")
    with pytest.raises(TrainingRequestIncompleteError):
        normalize_requirement_snapshot(missing)

    with pytest.raises(ExportFormatUnsupportedError):
        normalize_requirement_snapshot(snapshot(format="PARQUET"))

    with pytest.raises(TrainingRequestIncompleteError):
        normalize_requirement_snapshot(
            snapshot(target_episode_count=None, target_duration_ns=None)
        )

    with pytest.raises(TrainingRequestIncompleteError):
        normalize_requirement_snapshot(snapshot(quality_thresholds={"api_key": "x"}))

    with pytest.raises(ExportFormatVersionUnsupportedError):
        normalize_requirement_snapshot(snapshot(format_version="ddp.lerobot/9.9.9"))


def test_revision_hash_changes_with_snapshot_and_rejects_tamper() -> None:
    _, revision, _ = make_request()
    assert revision.revision_sha256 and revision.revision_sha256.startswith("sha256:")
    _, other, _ = make_request(target_episode_count=200)
    assert other.revision_sha256 != revision.revision_sha256


def test_approved_revision_is_immutable_and_cannot_self_approve() -> None:
    request, revision, actor = make_request()
    assert request.status is TrainingRequestStatus.DRAFT
    assert revision.status is RevisionStatus.DRAFT

    submitted = revision.submit(actor_id=actor, now=NOW)
    assert submitted.status is RevisionStatus.SUBMITTED
    pending = submitted.start_review(now=NOW)
    assert pending.status is RevisionStatus.PENDING_REVIEW

    with pytest.raises(TrainingRequestApprovalForbiddenError):
        pending.approve(actor_id=actor, now=NOW)

    approver = uuid4()
    approved = pending.approve(actor_id=approver, now=NOW)
    assert approved.status is RevisionStatus.APPROVED
    with pytest.raises(TrainingRequestImmutableError):
        approved.with_content(
            snapshot(business_goal="擅自修改"),
            change_reason="x",
            actor_id=approver,
            now=NOW,
        )
    with pytest.raises(InvalidStateTransitionError):
        approved.approve(actor_id=approver, now=NOW)


def test_request_terminal_state_cannot_be_rolled_back() -> None:
    request, _, actor = make_request()
    cancelled = request.apply_action(
        "cancel", now=NOW, actor_id=actor, reason="不再需要"
    )
    assert cancelled.status is TrainingRequestStatus.CANCELLED
    with pytest.raises(InvalidStateTransitionError):
        cancelled.apply_action("block", now=NOW, actor_id=actor, reason="x")


def _delivery(**overrides: object):
    values: dict[str, object] = {
        "training_delivery_id": uuid4(),
        "project_id": uuid4(),
        "training_request_id": uuid4(),
        "training_request_revision_id": uuid4(),
        "dataset_version_id": uuid4(),
        "export_job_id": uuid4(),
        "delivery_note_no": f"DN-{uuid4().hex[:8]}",
        "recipient_id": uuid4(),
        "recipient_team": "算法组",
        "artifact_manifest_key": "exports/manifest.json",
        "artifact_manifest_sha256": SHA,
        "aggregate_sha256": SHA_B,
        "item_count": 10,
        "split_counts": {"train": 8, "validation": 1, "test": 1},
        "quality_report_ref": SHA_B,
        "actor_id": uuid4(),
        "now": NOW,
    }
    values.update(overrides)
    return new_training_delivery(**values)


def test_delivery_lifecycle_accept_reject_rework_and_self_acceptance() -> None:
    delivery = _delivery()
    assert delivery.status is TrainingDeliveryStatus.DRAFT

    creator = delivery.created_by
    assert creator is not None
    ready = delivery.ready(now=NOW, actor_id=creator)
    delivered = ready.deliver(now=NOW, actor_id=creator)
    with pytest.raises(TrainingDeliveryAcceptanceForbiddenError):
        delivered.accept(now=NOW, actor_id=creator, opinion="通过")

    rejected = delivered.reject(now=NOW, actor_id=uuid4(), reason="划分不符")
    rework = rejected.rework(
        now=NOW,
        actor_id=creator,
        reason="重新导出",
        rework_ref=uuid4(),
    )
    assert rework.status is TrainingDeliveryStatus.REWORK
    assert rework.training_request_revision_id == delivery.training_request_revision_id

    second = _delivery(project_id=delivery.project_id)
    second_ready = second.ready(now=NOW, actor_id=creator)
    second_delivered = second_ready.deliver(now=NOW, actor_id=creator)
    accepted = second_delivered.accept(
        now=NOW,
        actor_id=uuid4(),
        opinion="校验和与验收单一致",
    )
    assert accepted.status is TrainingDeliveryStatus.ACCEPTED
    assert accepted.accepted_by is not None


def test_delivery_invariant_rejects_inconsistent_counts() -> None:
    with pytest.raises(DomainValidationError):
        _delivery(item_count=9)
    with pytest.raises(DomainValidationError):
        _delivery(split_counts={"train": 8, "validation": 1})


def test_grant_expiry_and_revocation_are_immediate() -> None:
    actor = uuid4()
    grant = new_dataset_grant(
        grant_id=uuid4(),
        project_id=uuid4(),
        dataset_id=uuid4(),
        dataset_version_id=None,
        grantee_project_id=uuid4(),
        grantee_team="算法组",
        scope=["VIEW"],
        reason="联合训练",
        expires_at=NOW + timedelta(hours=1),
        actor_id=actor,
        now=NOW,
    )
    with pytest.raises(DatasetGrantForbiddenError):
        grant.approve(actor_id=actor, now=NOW)
    active = grant.approve(actor_id=uuid4(), now=NOW)
    assert active.status is DatasetGrantStatus.ACTIVE
    assert active.allows(GrantScope.VIEW, NOW)
    assert not active.allows(GrantScope.EXPORT, NOW)

    later = NOW + timedelta(hours=2)
    assert active.status_at(later) is DatasetGrantStatus.EXPIRED
    from app.domain.datasets.training_errors import DatasetGrantExpiredError

    with pytest.raises(DatasetGrantExpiredError):
        active.allows(GrantScope.VIEW, later)

    revoked = active.revoke(actor_id=uuid4(), now=NOW, reason="合规撤回")
    assert revoked.status is DatasetGrantStatus.REVOKED
    from app.domain.datasets.training_errors import DatasetGrantRevokedError

    with pytest.raises(DatasetGrantRevokedError):
        revoked.allows(GrantScope.VIEW, NOW)


def test_external_reference_status_is_authoritative_and_terminal() -> None:
    actor = uuid4()
    reference = new_external_reference(
        external_ref_id=uuid4(),
        project_id=uuid4(),
        adapter_id="mock",
        adapter_version="1.0.0",
        training_delivery_id=uuid4(),
        training_request_revision_id=uuid4(),
        dataset_version_id=uuid4(),
        external_run_id="run-1",
        external_run_url=None,
        correlation_id="corr-1",
        idempotency_key="idem-0001",
        model_ref=None,
        evaluation_ref=None,
        actor_id=actor,
        now=NOW,
    )
    running = reference.apply_status(
        ExternalTrainingStatus.RUNNING, now=NOW, actor_id=actor
    )
    succeeded = running.apply_status(
        ExternalTrainingStatus.SUCCEEDED,
        now=NOW,
        actor_id=actor,
        model_ref={"model_id": "model-1"},
    )
    assert succeeded.effective_status is ExternalTrainingStatus.SUCCEEDED
    assert succeeded.model_ref == {"model_id": "model-1"}
    with pytest.raises(InvalidStateTransitionError):
        succeeded.apply_status(ExternalTrainingStatus.FAILED, now=NOW, actor_id=actor)


def test_callback_signature_window_nonce_and_replay_guards() -> None:
    secret = "s" * 32
    body = b'{"event_id":"e1"}'
    timestamp = int(NOW.timestamp())
    nonce = "nonce-0001"
    signature = compute_callback_signature(secret, timestamp, nonce, body)
    assert verify_callback_signature(
        secret=secret,
        timestamp=str(timestamp),
        nonce=nonce,
        signature=signature,
        body=body,
        now=NOW,
    ) == nonce
    assert verify_callback_signature(
        secret=secret,
        timestamp=str(timestamp),
        nonce=nonce,
        signature=signature.replace("sha256=", "sha256:"),
        body=body,
        now=NOW,
    ) == nonce

    with pytest.raises(TrainingCallbackInvalidError):
        verify_callback_signature(
            secret=secret,
            timestamp=str(timestamp),
            nonce=nonce,
            signature="sha256=" + "0" * 64,
            body=body,
            now=NOW,
        )
    with pytest.raises(TrainingCallbackInvalidError):
        verify_callback_signature(
            secret=secret,
            timestamp=str(timestamp - 301),
            nonce=nonce,
            signature=signature,
            body=body,
            now=NOW,
        )
    with pytest.raises(TrainingCallbackInvalidError):
        verify_callback_signature(
            secret=None,
            timestamp=str(timestamp),
            nonce=nonce,
            signature=signature,
            body=body,
            now=NOW,
        )
