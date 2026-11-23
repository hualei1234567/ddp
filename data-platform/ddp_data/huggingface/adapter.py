"""读取 Hugging Face 数据集元信息并规范化为 DDP 可入库的形状。

为什么单独一层：``huggingface_hub`` 的返回结构随版本变化，业务逻辑不应直接
依赖它。这里把"外部 API 形状"收敛到 :class:`DatasetCardInfo`，后端只认这个。

关键约束（`docs/ddp/07` §2）：

- **``revision`` 必须解析成不可变的 commit sha**。``main`` 会移动，只存 ``main``
  的导入不可复现。
- **``license`` 缺失时返回 ``None`` 而不是猜**。没有许可证声明时无法判断能否使用，
  调用方必须把它当作需要人工确认的状态。
- **``gated`` 必须如实反映**。访问成功不等于拥有商业使用权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

# HF repo id 形如 ``owner/name``；name 允许字母数字、点、下划线、连字符。
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")
# commit sha：40 位十六进制。
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# 常见许可证别名规范化：HF 上同一许可有多种写法。
_LICENSE_ALIASES = {
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "mit": "MIT",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "cc0-1.0": "CC0-1.0",
    "gpl-3.0": "GPL-3.0",
    "lgpl-3.0": "LGPL-3.0",
    "other": "other",
    "unknown": None,
    "": None,
}


class HfAdapterError(Exception):
    """外部源访问或元信息解析失败；调用方应记为导入失败而不是静默跳过。"""


@dataclass(frozen=True, slots=True)
class HfDatasetRef:
    """已解析并规范化的数据源引用。"""

    repo_id: str
    revision: str  # 已解析为 commit sha
    license: str | None
    is_gated: bool
    dataset_card_url: str


@dataclass(frozen=True, slots=True)
class DatasetCardInfo:
    """``HfRepoClient`` 的返回形状；与 ``huggingface_hub`` 解耦。"""

    repo_id: str
    sha: str | None
    card_data: dict[str, object]
    gated: object  # False / "auto" / "manual"
    tags: tuple[str, ...] = ()


class HfRepoClient(Protocol):
    """读取 HF 元信息的最小协议；生产与测试各给一个实现。"""

    def dataset_info(
        self, repo_id: str, *, revision: str | None = None
    ) -> DatasetCardInfo:
        """返回数据集元信息；仓库不存在或无权限时抛异常。"""
        ...


def parse_repo_id(value: str) -> str:
    """校验并规范化 ``owner/name``；不接受 URL 或带多余空白的输入。"""
    text = (value or "").strip()
    if not text:
        raise HfAdapterError("repo_id 不能为空")
    if text.startswith(("http://", "https://")):
        raise HfAdapterError("repo_id 应为 owner/name，不是 URL")
    if not _REPO_ID_RE.match(text):
        raise HfAdapterError(f"repo_id 格式非法：{text!r}（应为 owner/name）")
    return text


def resolve_license(card_data: dict[str, object]) -> str | None:
    """从 Dataset Card 取许可证；无法确定时返回 ``None``，绝不猜。"""
    raw = card_data.get("license")
    if isinstance(raw, list):
        # 多许可证时按 SPDX 习惯用 OR 连接；顺序保持原样以便复现。
        parts = [str(item).strip() for item in raw if str(item).strip()]
        if not parts:
            return None
        normalized = [_LICENSE_ALIASES.get(part.lower(), part) for part in parts]
        kept = [item for item in normalized if item]
        return " OR ".join(kept) if kept else None
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _LICENSE_ALIASES:
        return _LICENSE_ALIASES[text]
    return str(raw).strip() or None


def _is_gated(value: object) -> bool:
    """``gated`` 可能是 ``False``、``"auto"``、``"manual"``；后两者都算受限。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"auto", "manual", "true"}
    return bool(value)


class HuggingFaceAdapter:
    """把外部元信息规范化成 :class:`HfDatasetRef`。"""

    def __init__(self, client: HfRepoClient) -> None:
        self._client = client

    def describe(self, repo_id: str, *, revision: str | None = None) -> HfDatasetRef:
        normalized = parse_repo_id(repo_id)
        try:
            info = self._client.dataset_info(normalized, revision=revision)
        except HfAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - 外部库异常类型不稳定，统一收敛
            raise HfAdapterError(f"读取数据集元信息失败：{exc}") from exc

        sha = (info.sha or "").strip()
        if not _COMMIT_SHA_RE.match(sha):
            # 不能把 main/branch 当作可复现的 revision 存库。
            raise HfAdapterError(
                f"未能解析到不可变 commit sha（得到 {sha!r}）；拒绝以可变引用登记"
            )

        return HfDatasetRef(
            repo_id=normalized,
            revision=sha,
            license=resolve_license(info.card_data),
            is_gated=_is_gated(info.gated),
            dataset_card_url=f"https://huggingface.co/datasets/{normalized}",
        )
