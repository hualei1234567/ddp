"""``scripts/e2e/pilot_edge_chain.py`` 配套测试。

遵守 MEM-INC-030：顶层仅使用 Python 标准库与 ``pytest``，绝不顶层导入数据库驱动或
FastAPI，确保 CI 极简依赖环境（仅装 pytest/pip-audit/pyyaml）也能稳定执行 CLI 与
边界校验用例；含 FastAPI ``TestClient`` 的全链路集成测试通过 ``importorskip`` 按需执行。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "e2e" / "pilot_edge_chain.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "pilot_edge_chain_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pilot_script():
    return _load_script()


def test_validate_live_args_rejects_bad_scheme(pilot_script) -> None:
    args = pilot_script.build_parser().parse_args(["--live-url", "ftp://example.com"])
    with pytest.raises(ValueError, match="http:// 或 https://"):
        pilot_script.validate_live_args(args)


def test_validate_live_args_requires_project_upload_session_and_file(
    pilot_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_key in (
        "DDP_PROJECT_ID",
        "DDP_UPLOAD_TOKEN",
        "DDP_PAIRING_TOKEN",
        "DDP_AGENT_ID",
        "DDP_AGENT_TOKEN",
    ):
        monkeypatch.delenv(env_key, raising=False)

    parser = pilot_script.build_parser()
    # 缺 project-id
    with pytest.raises(ValueError, match="--project-id"):
        pilot_script.validate_live_args(
            parser.parse_args(["--live-url", "https://ddp.local"])
        )

    # 缺 upload-token
    with pytest.raises(ValueError, match="--upload-token"):
        pilot_script.validate_live_args(
            parser.parse_args(
                ["--live-url", "https://ddp.local", "--project-id", "proj-1"]
            )
        )

    # 缺配对码或已配对凭据
    sample = tmp_path / "demo.mcap"
    sample.write_bytes(b"\x89MCAP0\r\nhello")
    with pytest.raises(ValueError, match="--pairing-token"):
        pilot_script.validate_live_args(
            parser.parse_args(
                [
                    "--live-url",
                    "https://ddp.local",
                    "--project-id",
                    "proj-1",
                    "--upload-token",
                    "jwt-placeholder",
                    "--capture-session-id",
                    "cs-1",
                    "--file",
                    str(sample),
                ]
            )
        )

    # 全部齐全时通过
    cfg = pilot_script.validate_live_args(
        parser.parse_args(
            [
                "--live-url",
                "https://ddp.local/",
                "--project-id",
                "proj-1",
                "--upload-token",
                "jwt-placeholder",
                "--pairing-token",
                "pair-placeholder",
                "--capture-session-id",
                "cs-1",
                "--file",
                str(sample),
                "--no-verify-tls",
            ]
        )
    )
    assert cfg.live_url == "https://ddp.local"
    assert cfg.verify_tls is False
    assert cfg.file_path == sample.resolve()


def test_main_returns_exit_2_on_invalid_live_args(
    pilot_script, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = pilot_script.main(["--live-url", "https://ddp.local"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "ERROR"


def test_software_baseline_end_to_end_chain(pilot_script, tmp_path: Path) -> None:
    """进程内 FastAPI TestClient + SQLite + 真实 Edge Agent Spool/UploadDriver 全链路验证。"""
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("jwt")

    out_file = tmp_path / "pilot-baseline.json"
    rc = pilot_script.main(["--output", str(out_file)])
    assert rc == 0
    assert out_file.is_file()

    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["status"] == "PASSED"
    # 诚实边界：软件基线绝不伪造 A 级真机数据
    assert report["mode"] == "software_baseline_testclient"
    assert report["evidence_grade"] == "SOFTWARE_INTEGRATION_BASELINE"
    assert report["hardware_capture_status"] == "HARDWARE_CAPTURE_NOT_TESTED"
    assert report["real_hardware"] is False

    steps = report["steps"]
    assert steps["heartbeat_online"]["connectivity"] == "ONLINE"
    assert steps["capture_session_and_lease"]["lease_renewed"] is True
    assert steps["upload_and_raw_asset_verified"]["parts_uploaded"] >= 2
    assert steps["upload_and_raw_asset_verified"]["linked_to_capture_session"] is True
    assert steps["lease_released"]["status"] == "RELEASED"
