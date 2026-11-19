"""MOCK / DRY_RUN 训练执行器的领域规则。

真正的"执行"发生在应用层（``app.application.datasets.training_executors``），
本模块只定义**什么算真实训练结果**以及两种非真实执行器必须输出的诚实结构：

- ``NON_REAL_EXECUTION_MODES`` 在 ``training_enums`` 中唯一定义，下游据此拒绝
  把 MOCK/DRY_RUN 当作真实训练结果；
- ``build_mock_artifacts``：MOCK 只产出**显式标注为 mock** 的占位记录，绝不含
  可被误当成真实模型的对象键；
- ``build_dry_run_plan``：DRY_RUN 只做校验并输出将执行的动作清单，**不产生任何
  训练产物、不访问外部平台**。硬校验失败时抛 ``DomainValidationError``（不返回
  半成品计划），软问题进入 ``warnings`` 随计划一起落库。

本模块不做 I/O、不读配置、不访问网络；时间由调用方传入，输出有界。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasets.errors import DomainValidationError

MOCK_ARTIFACT_SCHEMA_VERSION = "ddp.mock-execution/1.0.0"
DRY_RUN_PLAN_SCHEMA_VERSION = "ddp.dry-run-plan/1.0.0"

# 输出边界：动作/检查项数量与序列化体积都设硬上限。执行器本身不访问网络，
# 没有 TCP 超时问题；这些上限防止调用方把无界对象塞进计划。
MAX_DRY_RUN_ACTIONS = 32
MAX_DRY_RUN_CHECKS = 32
MAX_DRY_RUN_WARNINGS = 32
MAX_PLAN_DETAIL_LENGTH = 500
MAX_PLAN_BYTES = 16 * 1024

# 一次真实训练会经历的动作。``external=True`` 表示真实执行会触及外部系统或
# 产生真实副作用；DRY_RUN 只列出它们，不执行。
_ACTIONS: tuple[tuple[str, str, bool], ...] = (
    ("validate_dataset_version", "校验交付引用的数据集版本与冻结清单", False),
    ("resolve_export_layout", "定位导出布局、Manifest 与媒体清单", False),
    ("validate_training_config", "校验训练配置键与取值", False),
    ("collect_training_samples", "读取抽帧与标签，构造训练样本", False),
    ("fit_model", "拟合模型（真实训练的核心计算）", False),
    ("verify_artifacts", "回读校验训练产物对象", False),
    ("upload_artifacts", "把模型/元数据写入对象存储", True),
    ("notify_callback", "向 DDP 发送签名回调", True),
)


@dataclass(frozen=True, slots=True)
class DryRunCheck:
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str

    def to_payload(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DryRunAction:
    name: str
    description: str
    external: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "external": self.external,
        }


@dataclass(frozen=True, slots=True)
class DryRunPlan:
    """演练计划：校验结论 + 将执行的动作清单。

    没有产物字段，因为它代表"如果真跑会发生什么"，不是训练结果本身。
    """

    adapter_id: str
    adapter_version: str
    dataset_version_id: UUID
    generated_at: datetime
    checks: tuple[DryRunCheck, ...]
    actions: tuple[DryRunAction, ...]
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": DRY_RUN_PLAN_SCHEMA_VERSION,
            "dry_run": True,
            "artifacts_produced": False,
            "external_platform_called": False,
            "generated_at": self.generated_at.isoformat(),
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "dataset_version_id": str(self.dataset_version_id),
            "checks": [check.to_payload() for check in self.checks],
            "actions": [action.to_payload() for action in self.actions],
            "warnings": list(self.warnings),
        }


def _clip(value: object, *, fallback: str) -> str:
    text = value if isinstance(value, str) else ""
    text = text.strip() or fallback
    return text[:MAX_PLAN_DETAIL_LENGTH]


def build_dry_run_plan(
    *,
    adapter_id: str,
    adapter_version: str,
    dataset_version_id: UUID,
    delivery_status: str,
    artifact_manifest_sha256: str | None,
    aggregate_sha256: str | None,
    config: Mapping[str, object],
    capabilities: Iterable[str],
    generated_at: datetime,
) -> DryRunPlan:
    """构建演练计划。硬校验失败抛 ``DomainValidationError``，不返回半成品计划。"""
    if not isinstance(dataset_version_id, UUID):
        raise DomainValidationError("演练计划必须引用数据集版本")
    declared = {str(item) for item in capabilities}
    checks: list[DryRunCheck] = [
        DryRunCheck(
            "dataset_version_reference",
            "PASS",
            _clip(f"数据集版本 {dataset_version_id}", fallback="数据集版本已引用"),
        )
    ]
    warnings: list[str] = []

    if delivery_status in {"DELIVERED", "ACCEPTED"}:
        checks.append(DryRunCheck("delivery_deliverable", "PASS", f"交付状态 {delivery_status}"))
    else:
        checks.append(
            DryRunCheck(
                "delivery_deliverable",
                "FAIL",
                f"交付状态 {delivery_status} 不允许提交训练（需 DELIVERED/ACCEPTED）",
            )
        )

    if artifact_manifest_sha256 and aggregate_sha256:
        checks.append(
            DryRunCheck("frozen_artifact_manifest", "PASS", "冻结产物清单与聚合校验和齐备")
        )
    else:
        checks.append(
            DryRunCheck(
                "frozen_artifact_manifest",
                "FAIL",
                "缺少冻结产物清单或聚合校验和，无法确定训练数据",
            )
        )

    if config:
        checks.append(DryRunCheck("training_config", "PASS", f"已提供 {len(config)} 个配置项"))
    else:
        checks.append(
            DryRunCheck("training_config", "WARN", "未提供训练配置，真实执行将使用默认值")
        )
        warnings.append("训练配置为空：真实执行将使用适配器默认值，请在提交前确认。")

    if "submit" in declared:
        checks.append(DryRunCheck("adapter_capabilities", "PASS", "Adapter 声明了 submit 能力"))
    elif not declared:
        checks.append(
            DryRunCheck(
                "adapter_capabilities",
                "WARN",
                "Adapter 未声明任何能力；真实执行会因适配器不可用而失败",
            )
        )
        warnings.append("Adapter 未声明能力：演练通过不代表真实执行可用。")
    else:
        checks.append(
            DryRunCheck(
                "adapter_capabilities",
                "FAIL",
                "Adapter 声明了能力但缺少 submit，真实执行会被拒绝",
            )
        )

    failed = [check for check in checks if check.status == "FAIL"]
    if failed:
        details = "；".join(f"{check.name}: {check.detail}" for check in failed)
        raise DomainValidationError(f"演练校验未通过：{details}")

    actions = tuple(
        DryRunAction(name=name, description=description, external=external)
        for name, description, external in _ACTIONS
    )
    if len(actions) > MAX_DRY_RUN_ACTIONS or len(checks) > MAX_DRY_RUN_CHECKS:
        raise DomainValidationError("演练计划超出动作/检查项数量上限")
    if len(warnings) > MAX_DRY_RUN_WARNINGS:
        raise DomainValidationError("演练计划超出告警数量上限")
    plan = DryRunPlan(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        dataset_version_id=dataset_version_id,
        generated_at=generated_at,
        checks=tuple(checks),
        actions=actions,
        warnings=tuple(warnings),
    )
    _assert_bounded(plan.to_payload())
    return plan


def _assert_bounded(payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_PLAN_BYTES:
        raise DomainValidationError(f"演练计划超过 {MAX_PLAN_BYTES} 字节上限")


def build_mock_artifacts(
    *,
    adapter_id: str,
    adapter_version: str,
    run_id: str,
    delivery_id: UUID,
    dataset_version_id: UUID,
    generated_at: datetime,
) -> dict[str, object]:
    """MOCK 产物的诚实标注。

    关键约束：**不提供** ``model.object_key`` / ``sha256`` / ``size_bytes`` 这类
    看起来可登记为模型版本的字段。任何下游若只看 ``artifacts.model`` 会得到
    "无产物"，再叠加 ``mock=true`` 与 ``execution_mode=MOCK`` 必然拒绝。
    """
    return {
        "schema_version": MOCK_ARTIFACT_SCHEMA_VERSION,
        "mock": True,
        "execution_mode": "MOCK",
        "materialized": False,
        "note": "MOCK 执行器产生的占位记录，不是真实训练产物，不得登记为模型版本。",
        "run_id": run_id,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "training_delivery_id": str(delivery_id),
        "dataset_version_id": str(dataset_version_id),
        "generated_at": generated_at.isoformat(),
    }


__all__ = [
    "DRY_RUN_PLAN_SCHEMA_VERSION",
    "DryRunAction",
    "DryRunCheck",
    "DryRunPlan",
    "MAX_DRY_RUN_ACTIONS",
    "MAX_PLAN_BYTES",
    "MOCK_ARTIFACT_SCHEMA_VERSION",
    "build_dry_run_plan",
    "build_mock_artifacts",
]
