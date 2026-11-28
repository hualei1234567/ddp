"""online_deploy_verify.py 单元测试（遵循 MEM-INC-030：顶层仅依赖标准库与 pytest）。"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ops" / "online_deploy_verify.py"
COMPOSE_PROD_PATH = REPO_ROOT / "infra" / "deploy" / "docker-compose.prod.yml"
NGINX_CONF_PATH = REPO_ROOT / "infra" / "deploy" / "nginx-origin.conf.example"
ENV_EXAMPLE_PATH = REPO_ROOT / "infra" / "deploy" / "env.production.example"


def _load_module():
    spec = importlib.util.spec_from_file_location("online_deploy_verify", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_prod_env() -> dict[str, str]:
    return {
        "DDP_ENV": "production",
        "DDP_LOCAL_HEADER_AUTH": "false",
        "DDP_JWT_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "DDP_JWT_ISSUER": "ddp",
        "DDP_JWT_AUDIENCE": "ddp-api",
        "DDP_DB_PASSWORD": "prod_db_password_9f8e7d6c5b4a",
        "DDP_S3_ACCESS_KEY": "ddp_minio_prod_ak",
        "DDP_S3_SECRET_KEY": "prod_s3_secret_9f8e7d6c5b4a3210",
        "DDP_CORS_ALLOWED_ORIGINS": '["https://www.ddp.online"]',
        "DDP_RATE_LIMIT_BACKEND": "redis",
        "DDP_RATE_LIMIT_REDIS_URL": "redis://redis:6379/0",
        "DDP_METRICS_ACCESS_TOKEN": "m1n2o3p4q5r6m1n2o3p4q5r6m1n2o3p4m1n2",
        "DDP_HSTS_ENABLED": "true",
        "DDP_HSTS_MAX_AGE_SECONDS": "31536000",
        "DDP_MAINTENANCE_ENABLED": "true",
        "DDP_BACKUP_DIR": "/backups",
        "DDP_TRAINING_ADAPTER_ALLOW_PRIVATE": "false",
        "DDP_MODEL_ADAPTER_ALLOW_PRIVATE": "false",
        "DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE": "false",
    }


def test_top_level_imports_are_stdlib_only() -> None:
    """MEM-INC-030 守卫：顶层严禁导入 httpx/yaml/sqlalchemy/psycopg。"""
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    forbidden = {"httpx", "yaml", "sqlalchemy", "psycopg", "jwt", "pydantic"}
    top_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_imports.add(node.module.split(".")[0])
    assert top_imports.isdisjoint(
        forbidden
    ), f"发现禁止的顶层导入: {top_imports & forbidden}"


def test_verify_env_map_passes_for_hardened_prod_env() -> None:
    mod = _load_module()
    results = mod.verify_env_map(_valid_prod_env())
    failed = [r for r in results if not r.passed]
    assert not failed, f"预期全部通过，实际失败: {failed}"


def test_env_production_example_fails_placeholder_checks() -> None:
    """未替换占位符的模板文件必须被拦截，防止带占位符上线。"""
    mod = _load_module()
    env = mod.parse_env_file(ENV_EXAMPLE_PATH)
    results = {r.name: r.passed for r in mod.verify_env_map(env)}
    assert results["env.jwt_secret"] is False
    assert results["env.s3_credentials"] is False
    assert results["env.db_password"] is False
    assert results["env.metrics_token"] is False
    assert results["env.cors_origins"] is True
    assert results["env.ssrf_private_guards"] is True


@pytest.mark.parametrize(
    ("overrides", "expected_failed"),
    [
        ({"DDP_ENV": "local"}, "env.ddp_env"),
        ({"DDP_LOCAL_HEADER_AUTH": "true"}, "env.local_header_auth"),
        ({"DDP_JWT_SECRET": "too-short"}, "env.jwt_secret"),
        ({"DDP_S3_ACCESS_KEY": "minioadmin"}, "env.s3_credentials"),
        (
            {
                "DDP_S3_ACCESS_KEY": "same_value_1234",
                "DDP_S3_SECRET_KEY": "same_value_1234",
            },
            "env.s3_credentials",
        ),
        ({"DDP_CORS_ALLOWED_ORIGINS": '["*"]'}, "env.cors_origins"),
        (
            {"DDP_CORS_ALLOWED_ORIGINS": '["https://www.ddp.online/"]'},
            "env.cors_origins",
        ),
        ({"DDP_RATE_LIMIT_REDIS_URL": "http://redis:6379/0"}, "env.rate_limit_redis"),
        ({"DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE": "true"}, "env.ssrf_private_guards"),
        ({"DDP_METRICS_ACCESS_TOKEN": "short"}, "env.metrics_token"),
    ],
)
def test_verify_env_map_catches_security_redlines(
    overrides: dict[str, str], expected_failed: str
) -> None:
    mod = _load_module()
    env = _valid_prod_env()
    env.update(overrides)
    results = {r.name: r for r in mod.verify_env_map(env)}
    assert results[expected_failed].passed is False


def test_verify_compose_prod_passes_for_repo_file() -> None:
    pytest.importorskip("yaml")
    mod = _load_module()
    results = mod.verify_compose_prod(COMPOSE_PROD_PATH)
    failed = [r for r in results if not r.passed]
    assert not failed, f"compose 生产覆盖校验失败: {failed}"


def test_verify_compose_prod_detects_exposed_ports_and_missing_worker_s3(
    tmp_path: Path,
) -> None:
    pytest.importorskip("yaml")
    mod = _load_module()
    bad_compose = tmp_path / "docker-compose.bad.yml"
    bad_compose.write_text(
        """
services:
  postgres:
    ports: ["5432:5432"]
  redis:
    ports: !override []
  minio:
    ports: !override ["0.0.0.0:9000:9000"]
  api:
    ports: !override ["8000:8000"]
    environment:
      DDP_DATABASE_URL: postgresql+psycopg://ddp:ddp@postgres:5432/ddp
      DDP_S3_ENDPOINT: http://minio:9000
  worker:
    environment:
      DDP_S3_ENDPOINT: https://s3.ddp.online
""",
        encoding="utf-8",
    )
    results = {r.name: r.passed for r in mod.verify_compose_prod(bad_compose)}
    assert results["compose.postgres_no_public_ports"] is False
    assert results["compose.minio_loopback_only"] is False
    assert results["compose.api_loopback_only"] is False
    assert results["compose.api_public_s3_endpoint"] is False
    assert results["compose.api_db_url_override"] is False
    assert results["compose.workers_internal_s3"] is False


def test_verify_nginx_conf_passes_for_repo_file() -> None:
    mod = _load_module()
    results = mod.verify_nginx_conf(NGINX_CONF_PATH)
    failed = [r for r in results if not r.passed]
    assert not failed, f"nginx 配置校验失败: {failed}"


def test_verify_nginx_conf_detects_missing_s3_cors(tmp_path: Path) -> None:
    mod = _load_module()
    bad_nginx = tmp_path / "nginx.conf"
    bad_nginx.write_text(
        "server { listen 80; server_name api.ddp.online; }", encoding="utf-8"
    )
    results = {r.name: r.passed for r in mod.verify_nginx_conf(bad_nginx)}
    assert results["nginx.s3_upstream"] is False
    assert results["nginx.s3_cors_preflight"] is False
    assert results["nginx.tls_443"] is False


def test_verify_online_endpoints_passes_with_mock_requester() -> None:
    mod = _load_module()

    def fake_requester(method: str, url: str, headers: dict[str, str], timeout: float):
        assert timeout > 0
        if method == "GET" and url == "https://api.ddp.online/health":
            return mod.HttpProbeResponse(
                200, {"Content-Type": "application/json"}, '{"status":"ok"}'
            )
        if method == "OPTIONS" and url == "https://api.ddp.online/api/v1/me":
            return mod.HttpProbeResponse(
                200,
                {"Access-Control-Allow-Origin": headers["Origin"]},
                "",
            )
        if method == "GET" and url in {
            "https://www.ddp.online/",
            "https://www.ddp.online/assets",
        }:
            return mod.HttpProbeResponse(
                200, {"Content-Type": "text/html; charset=utf-8"}, "<html>"
            )
        if method == "OPTIONS" and url == "https://s3.ddp.online/":
            return mod.HttpProbeResponse(
                204,
                {
                    "access-control-allow-origin": headers["Origin"],
                    "Access-Control-Allow-Methods": "GET, PUT, HEAD, POST, DELETE, OPTIONS",
                },
                "",
            )
        raise AssertionError(f"未预期请求: {method} {url}")

    results = mod.verify_online_endpoints(
        api_url="https://api.ddp.online",
        web_url="https://www.ddp.online",
        s3_url="https://s3.ddp.online",
        requester=fake_requester,
    )
    assert len(results) == 5
    assert all(r.passed for r in results), f"预期全部在线探测通过，实际: {results}"


def test_verify_online_endpoints_reports_failures_and_exceptions() -> None:
    mod = _load_module()

    def failing_requester(
        method: str, url: str, headers: dict[str, str], timeout: float
    ):
        if url.endswith("/health"):
            return mod.HttpProbeResponse(503, {}, "unavailable")
        if url.endswith("/api/v1/me"):
            return mod.HttpProbeResponse(
                200, {"Access-Control-Allow-Origin": "https://evil.test"}, ""
            )
        if url.endswith("/assets"):
            return mod.HttpProbeResponse(
                404, {"Content-Type": "text/plain"}, "not found"
            )
        if url == "https://www.ddp.online/":
            return mod.HttpProbeResponse(200, {"Content-Type": "text/html"}, "<html>")
        raise ConnectionError("dns timeout")

    results = {
        r.name: r
        for r in mod.verify_online_endpoints(
            api_url="https://api.ddp.online",
            web_url="https://www.ddp.online",
            s3_url="https://s3.ddp.online",
            requester=failing_requester,
        )
    }
    assert results["online.api_health"].passed is False
    assert results["online.api_cors"].passed is False
    assert results["online.web_index"].passed is True
    assert results["online.web_spa_assets_fallback"].passed is False
    assert results["online.s3_cors_options"].passed is False
    assert "ConnectionError" in results["online.s3_cors_options"].detail


def test_cli_main_skip_env_json_passes_on_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("yaml")
    mod = _load_module()
    exit_code = mod.main(["--skip-env", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["failed"] == 0
    assert payload["passed"] >= 10
