"""``scripts/e2e/v61_training_closed_loop.py`` 配套测试。

遵守 MEM-INC-030：顶层仅使用 Python 标准库与 ``pytest``，绝不顶层导入 FastAPI、
SQLAlchemy 或 PyJWT，确保 CI 极简依赖环境也能稳定执行 CLI、scheme 校验与
evidence payload 转换测试；含 FastAPI ``TestClient`` 的进程内全闭环用例通过
``pytest.importorskip`` 按需执行。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "e2e" / "v61_training_closed_loop.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "v61_training_closed_loop_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def closed_loop_script():
    return _load_script()


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "ftp://ddp.local",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "https://",
        "ddp.local:8000",
    ],
)
def test_validate_http_url_rejects_invalid_schemes(
    closed_loop_script, bad_url: str
) -> None:
    with pytest.raises(ValueError, match="http:// 或 https://"):
        closed_loop_script.validate_http_url(bad_url)


def test_validate_live_args_checks_required_fields_and_returns_config(
    closed_loop_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_key in (
        "DDP_PROJECT_ID",
        "DDP_API_TOKEN",
        "DDP_APPROVER_TOKEN",
        "DDP_EXTERNAL_REF_ID",
        "DDP_DATASET_VERSION_ID",
        "DDP_MODEL_VERSION_ID",
        "DDP_MODEL_ADAPTER_SECRET",
    ):
        monkeypatch.delenv(env_key, raising=False)

    parser = closed_loop_script.build_parser()

    with pytest.raises(ValueError, match="http:// 或 https://"):
        closed_loop_script.validate_live_args(
            parser.parse_args(["--live-url", "ftp://example.com"])
        )

    with pytest.raises(ValueError, match="--project-id"):
        closed_loop_script.validate_live_args(
            parser.parse_args(["--live-url", "https://ddp.local"])
        )

    with pytest.raises(ValueError, match="--auth-token"):
        closed_loop_script.validate_live_args(
            parser.parse_args(
                ["--live-url", "https://ddp.local", "--project-id", "proj-1"]
            )
        )

    with pytest.raises(ValueError, match="--external-ref-id"):
        closed_loop_script.validate_live_args(
            parser.parse_args(
                [
                    "--live-url",
                    "https://ddp.local",
                    "--project-id",
                    "proj-1",
                    "--auth-token",
                    "tok-1",
                ]
            )
        )

    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text("{}", encoding="utf-8")

    cfg = closed_loop_script.validate_live_args(
        parser.parse_args(
            [
                "--live-url",
                "https://ddp.local/",
                "--project-id",
                "proj-1",
                "--auth-token",
                "tok-editor",
                "--approver-token",
                "tok-owner",
                "--external-ref-id",
                "ref-1",
                "--dataset-version-id",
                "ver-1",
                "--model-version-id",
                "mv-1",
                "--evidence-file",
                str(evidence_file),
                "--run-kind",
                "REAL",
            ]
        )
    )
    assert cfg.live_url == "https://ddp.local"
    assert cfg.project_id == "proj-1"
    assert cfg.auth_token == "tok-editor"
    assert cfg.approver_token == "tok-owner"
    assert cfg.run_kind == "REAL"
    assert cfg.evidence_file == evidence_file.resolve()


def test_load_training_evidence_payload_reuses_gate_b_spec(
    closed_loop_script,
) -> None:
    raw, payload = closed_loop_script.load_training_evidence_payload(
        closed_loop_script.DEFAULT_EVIDENCE_FILE, run_kind="SMOKE"
    )
    assert raw["gate"] == "GATE-B"
    assert payload["run_kind"] == "SMOKE"
    assert payload["device"] == "cpu"
    assert payload["steps"] == 2
    assert payload["exit_code"] == 0
    assert payload["config_sha256"].startswith("sha256:")
    assert payload["checkpoint_sha256"].startswith("sha256:")
    assert payload["runtime_versions"]["lerobot"] == "0.3.3"


def test_main_returns_exit_2_on_invalid_live_args(
    closed_loop_script, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = closed_loop_script.main(["--live-url", "file:///tmp/bad"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "ERROR"


def test_in_process_closed_loop_end_to_end(closed_loop_script, tmp_path: Path) -> None:
    """进程内 FastAPI TestClient + 内存 SQLite 全闭环端到端断言。"""
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("jwt")

    out_file = tmp_path / "closed-loop-result.json"
    rc = closed_loop_script.main(["--output", str(out_file)])
    assert rc == 0
    assert out_file.is_file()

    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["status"] == "PASSED"
    assert report["mode"] == "in_process_testclient_sqlite"
    assert report["task_id"] == "T3-04"

    steps = report["steps"]
    assert steps["dataset_and_export"]["dataset_version_status"] == "FROZEN"
    assert steps["dataset_and_export"]["export_status"] == "SUCCEEDED"
    assert steps["training_delivery_and_reference"]["delivery_status"] == "DELIVERED"
    assert steps["training_delivery_and_reference"]["execution_mode"] == "LOCAL"

    ev = steps["training_evidence"]
    assert ev["run_kind"] == "SMOKE"
    assert ev["status"] == "SUCCEEDED"
    assert ev["exit_code"] == 0
    assert ev["checkpoint_sha256"].startswith("sha256:")
    assert ev["verified_by_reference_count"] >= 1
    assert ev["verified_by_delivery_count"] >= 1

    assert steps["model_registry"]["status"] == "READY"
    assert steps["model_registry"]["artifact_verification_status"] == "VERIFIED"

    assert steps["initial_evaluation"]["status"] == "SUCCEEDED"
    assert steps["initial_evaluation"]["verdict"] == "FAIL"

    assert steps["badcase_feedback"]["status"] == "OPEN"
    assert steps["badcase_feedback"]["gap_open_feedback_count"] >= 1

    assert steps["data_requirement"]["status_after_satisfy"] == "SATISFIED"
    assert steps["retest_and_link"]["retest_verdict"] == "PASS"
    assert (
        steps["retest_and_link"]["retest_evaluation_id"]
        in steps["retest_and_link"]["requirement_retest_ids"]
    )

    assert steps["closure"]["requirement_status"] == "CLOSED"
    assert steps["closure"]["feedback_status"] == "RESOLVED"
