"""HF Adapter 单元测试：不联网，用 fake 客户端注入。

重点验证三条红线（`docs/ddp/07` §2）：
1. revision 必须是不可变 commit sha，分支名一律拒绝；
2. 许可证缺失返回 None，绝不猜；
3. gated 如实反映。
"""

from __future__ import annotations

import os

import pytest
from ddp_data.huggingface import (
    DatasetCardInfo,
    HfAdapterError,
    HuggingFaceAdapter,
    parse_repo_id,
    resolve_license,
)

_SHA = "0123456789abcdef0123456789abcdef01234567"


class FakeClient:
    def __init__(self, info: DatasetCardInfo | Exception) -> None:
        self._info = info
        self.calls: list[tuple[str, str | None]] = []

    def dataset_info(self, repo_id: str, *, revision: str | None = None) -> DatasetCardInfo:
        self.calls.append((repo_id, revision))
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _info(**overrides: object) -> DatasetCardInfo:
    base: dict[str, object] = {
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "sha": _SHA,
        "card_data": {"license": "apache-2.0"},
        "gated": False,
    }
    base.update(overrides)
    return DatasetCardInfo(**base)  # type: ignore[arg-type]


def test_describe_returns_immutable_revision() -> None:
    adapter = HuggingFaceAdapter(FakeClient(_info()))

    ref = adapter.describe("lerobot/aloha_sim_insertion_human")

    assert ref.revision == _SHA
    assert ref.license == "Apache-2.0"
    assert ref.is_gated is False
    assert ref.dataset_card_url.endswith("lerobot/aloha_sim_insertion_human")


def test_missing_sha_is_rejected() -> None:
    """拿不到 commit sha 时必须失败，不能用 main 充当 revision。"""
    adapter = HuggingFaceAdapter(FakeClient(_info(sha="")))

    with pytest.raises(HfAdapterError, match="commit sha"):
        adapter.describe("lerobot/aloha_sim_insertion_human")


def test_branch_name_as_sha_is_rejected() -> None:
    adapter = HuggingFaceAdapter(FakeClient(_info(sha="main")))

    with pytest.raises(HfAdapterError, match="commit sha"):
        adapter.describe("lerobot/aloha_sim_insertion_human")


@pytest.mark.parametrize("gated", ["auto", "manual", True])
def test_gated_variants_are_all_flagged(gated: object) -> None:
    adapter = HuggingFaceAdapter(FakeClient(_info(gated=gated)))

    assert adapter.describe("lerobot/aloha_sim_insertion_human").is_gated is True


def test_missing_license_is_none_not_guessed() -> None:
    adapter = HuggingFaceAdapter(FakeClient(_info(card_data={})))

    assert adapter.describe("lerobot/aloha_sim_insertion_human").license is None


def test_unknown_license_string_is_preserved_verbatim() -> None:
    adapter = HuggingFaceAdapter(FakeClient(_info(card_data={"license": "custom-ddp-1.0"})))

    assert adapter.describe("lerobot/aloha_sim_insertion_human").license == "custom-ddp-1.0"


def test_client_exception_becomes_adapter_error() -> None:
    adapter = HuggingFaceAdapter(FakeClient(RuntimeError("401 unauthorized")))

    with pytest.raises(HfAdapterError, match="读取数据集元信息失败"):
        adapter.describe("lerobot/aloha_sim_insertion_human")


def test_revision_is_passed_through_to_client() -> None:
    client = FakeClient(_info())
    HuggingFaceAdapter(client).describe("lerobot/aloha_sim_insertion_human", revision=_SHA)

    assert client.calls == [("lerobot/aloha_sim_insertion_human", _SHA)]


# --- repo_id 与许可证规范化 -------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["lerobot/aloha_sim_insertion_human", "owner/name.with-dots_and-dashes"],
)
def test_valid_repo_ids(value: str) -> None:
    assert parse_repo_id(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "no-slash", "https://huggingface.co/datasets/lerobot/x", "a/b/c", "a/ b"],
)
def test_invalid_repo_ids(value: str) -> None:
    with pytest.raises(HfAdapterError):
        parse_repo_id(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"license": "apache-2.0"}, "Apache-2.0"),
        ({"license": "MIT"}, "MIT"),
        ({"license": ["apache-2.0", "mit"]}, "Apache-2.0 OR MIT"),
        ({"license": "unknown"}, None),
        ({"license": ""}, None),
        ({}, None),
    ],
)
def test_resolve_license(raw: dict[str, object], expected: str | None) -> None:
    assert resolve_license(raw) == expected


@pytest.mark.manual
def test_real_huggingface_metadata_manual() -> None:
    """可选的真实调用：默认跳过，只有显式开启才访问外网。

    常规测试一律用 fake 客户端；这条用例用于人工确认 ``huggingface_hub``
    版本升级或网络策略变化后生产客户端仍能读取元信息。
    """
    if os.environ.get("DDP_RUN_REMOTE_VERIFY") != "1":
        pytest.skip("手动用例：设置 DDP_RUN_REMOTE_VERIFY=1 才会真实访问 huggingface.co")
    pytest.importorskip("huggingface_hub")
    from ddp_data.huggingface.hub_client import HuggingFaceHubClient

    ref = HuggingFaceAdapter(HuggingFaceHubClient(timeout_seconds=10.0)).describe(
        "lerobot/aloha_sim_insertion_human"
    )

    assert len(ref.revision) == 40
    assert ref.is_gated is False
