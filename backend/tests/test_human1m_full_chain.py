"""Human-1M 10 条验收脚本的纯逻辑测试。

脚本本身要连真实 HF / PostgreSQL / MinIO，不能进 CI 常规回归；但其中
"解析官方校验和清单"与"双判据对照"是不依赖外部系统的纯逻辑，错误会让
验收结论失真（例如清单解析错位 → 静默比对到空集合），因此单独锁住。

只测纯函数，不触发 :func:`main`。

**位置说明**：本文件放在 `backend/tests/` 而不是 `scripts/tests/`，因为
脚本顶层 `import httpx` 以及 `app.*` 一整套后端依赖。CI 的
「Secret / 依赖 / Agent 守卫」job 只装 `pytest pip-audit`，在那里导入会
`ModuleNotFoundError`。放在 backend/tests 后可复用后端完整的依赖环境。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# 从 backend/tests/ 回到仓库根，再进 scripts/e2e/。
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "human1m-full-chain.py"
spec = importlib.util.spec_from_file_location("human1m_full_chain", SCRIPT_PATH)
assert spec and spec.loader
chain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain)

_SHA_A = "b8ce1beb64c14100dd2661fbfa15ebb608c3c96a0ff20720763c5c325c62b21d"
_SHA_B = "a150c17cb564f231eaa35545c11b6cc28f4f547a71f1c22a7fb3f8ad9fcc2e72"


def test_manifest_parses_official_format() -> None:
    """官方格式是 `<sha256>  <相对路径>`（两个空格）。"""
    text = f"{_SHA_A}  preview/videos/demo_001_0b4a67b0_main.mp4\n{_SHA_B}  DATASET_CARD.md\n"

    entries = chain.parse_checksum_manifest(text)

    assert entries == {
        "preview/videos/demo_001_0b4a67b0_main.mp4": _SHA_A,
        "DATASET_CARD.md": _SHA_B,
    }


def test_manifest_skips_blank_lines() -> None:
    entries = chain.parse_checksum_manifest(f"\n{_SHA_A}  a.mp4\n\n")

    assert entries == {"a.mp4": _SHA_A}


def test_manifest_rejects_short_digest() -> None:
    """截断的 hash 必须失败，否则比对会静默变成"全部不一致"或"全部一致"。"""
    with pytest.raises(ValueError, match="格式非法"):
        chain.parse_checksum_manifest("deadbeef  a.mp4")


def test_manifest_rejects_missing_path() -> None:
    with pytest.raises(ValueError, match="格式非法"):
        chain.parse_checksum_manifest(_SHA_A)


def test_manifest_rejects_empty_document() -> None:
    """空清单不能当成"无需校验"通过。"""
    with pytest.raises(ValueError, match="为空"):
        chain.parse_checksum_manifest("\n\n")


def test_expected_cameras_covers_acceptance_size() -> None:
    """人工期望值必须与验收口径 10 条 / 30 个资产自洽。"""
    assert len(chain.EXPECTED_CAMERAS) == chain.ACCEPTANCE_EPISODES
    assert chain.EXPECTED_ASSETS == 30
    assert chain.EXPECTED_ASSETS == sum(len(cameras) for cameras in chain.EXPECTED_CAMERAS.values())


def test_every_expected_episode_has_main_camera() -> None:
    """main 是第一人称主视角，也是质量的必备项；缺了必然全链失败。"""
    for key, cameras in chain.EXPECTED_CAMERAS.items():
        assert "main" in cameras, key


def test_official_url_uses_pinned_revision() -> None:
    """URL 必须带 40 位 commit sha，不能落到 main。"""
    url = chain._official_url("preview/metadata.jsonl")

    assert chain.REVISION in url
    assert "/main/" not in url
    assert len(chain.REVISION) == 40
