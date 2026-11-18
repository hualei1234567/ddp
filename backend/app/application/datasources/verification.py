"""数据源远端校验的端口与结论模型。

登记数据源时，``verify_remote=true`` 会让用例先读一次 Hugging Face 元信息，
但**只把规范化后的校验结论**写进既有 ``metadata`` 字段，绝不把远端返回的任意
字段当作业务权威值落库。

为什么单独一层：

- 用例依赖的是 :class:`RemoteSourceVerifier` 协议，而不是 ``ddp_data`` 或
  ``huggingface_hub``；测试可以注入 fake，生产实现放在 infrastructure；
- 远端结论的形状收敛为 :class:`RemoteCheckSummary`，前端/详情 API 只认这个，
  不直接读 ``metadata`` 里可能被人工改写的任意键。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Protocol

from app.domain.datasources.models import DataSource, DataSourceImport

# ``metadata`` 中服务端保留的键。客户端传入同名键会被拒绝，避免伪造"已校验"。
REMOTE_CHECK_METADATA_KEY = "remote_check"
REMOTE_CHECK_STATUS_VERIFIED: Final[Literal["VERIFIED"]] = "VERIFIED"
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RemoteCheckError(Exception):
    """远端元信息读取失败：超时、不存在、无权限、返回形状不可用等。

    调用方必须把它当作登记失败，不得降级为"未校验但继续登记"。
    """


@dataclass(frozen=True, slots=True)
class RemoteCheckResult:
    """一次远端校验的规范化结果（只含 adapter 已收敛的字段）。"""

    repo_id: str
    resolved_revision: str
    license: str | None
    is_gated: bool
    dataset_card_url: str


class RemoteSourceVerifier(Protocol):
    """读取外部数据源元信息的端口；生产实现基于 ``huggingface_hub``。"""

    def describe(self, repo_id: str, *, revision: str) -> RemoteCheckResult: ...


@dataclass(frozen=True, slots=True)
class RemoteCheckSummary:
    """落库并对外展示的校验结论。"""

    checked_at: datetime
    resolved_revision: str
    license: str | None
    is_gated: bool
    dataset_card_url: str | None


@dataclass(frozen=True, slots=True)
class DataSourceDetail:
    """数据源详情聚合：登记信息 + 导入任务 + 最近校验结论。"""

    source: DataSource
    imports: list[DataSourceImport]
    remote_check: RemoteCheckSummary | None


def build_remote_check_metadata(
    result: RemoteCheckResult,
    *,
    checked_at: datetime,
) -> dict[str, object]:
    """把规范化结论压成可存储的摘要；字段全部由 adapter 产出。"""
    return {
        "status": REMOTE_CHECK_STATUS_VERIFIED,
        "checked_at": checked_at.isoformat(),
        "resolved_revision": result.resolved_revision,
        "license": result.license,
        "is_gated": result.is_gated,
        "dataset_card_url": result.dataset_card_url,
    }


def read_remote_check(metadata: Mapping[str, object]) -> RemoteCheckSummary | None:
    """从 ``metadata`` 读取校验摘要。

    缺失返回 ``None``（未校验）。形状不合法也返回 ``None``：该键可能被历史
    人工数据污染，不能把坏值渲染成"已校验"。写入侧已由
    :func:`build_remote_check_metadata` 保证形状。
    """
    raw = metadata.get(REMOTE_CHECK_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    if raw.get("status") != REMOTE_CHECK_STATUS_VERIFIED:
        return None

    checked_at_raw = raw.get("checked_at")
    resolved_revision = raw.get("resolved_revision")
    if not isinstance(checked_at_raw, str) or not isinstance(resolved_revision, str):
        return None
    if not _COMMIT_SHA_RE.match(resolved_revision):
        return None
    try:
        checked_at = datetime.fromisoformat(checked_at_raw)
    except ValueError:
        return None
    # 无时区的时间无法判断"何时校验"，按无效处理而不是猜成本地时间。
    if checked_at.tzinfo is None:
        return None

    license_value = raw.get("license")
    if license_value is not None and not isinstance(license_value, str):
        return None
    is_gated = raw.get("is_gated")
    if not isinstance(is_gated, bool):
        return None
    dataset_card_url = raw.get("dataset_card_url")
    if dataset_card_url is not None and not isinstance(dataset_card_url, str):
        return None

    return RemoteCheckSummary(
        checked_at=checked_at,
        resolved_revision=resolved_revision,
        license=license_value,
        is_gated=is_gated,
        dataset_card_url=dataset_card_url,
    )


__all__ = [
    "REMOTE_CHECK_METADATA_KEY",
    "REMOTE_CHECK_STATUS_VERIFIED",
    "DataSourceDetail",
    "RemoteCheckError",
    "RemoteCheckResult",
    "RemoteCheckSummary",
    "RemoteSourceVerifier",
    "build_remote_check_metadata",
    "read_remote_check",
]
