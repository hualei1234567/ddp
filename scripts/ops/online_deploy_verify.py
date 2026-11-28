#!/usr/bin/env python3
"""DDP 公网线上部署上线前自检与公网冒烟验证脚本（www.ddp.online）。

支持离线模式（.env 安全红线 + docker-compose.prod.yml + nginx-origin.conf.example）
与在线模式（--api-url / --s3-url / --web-url 冒烟探测）。
严格遵循 MEM-INC-030：顶层仅导入 Python 标准库，yaml/httpx 均在函数内惰性导入。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_COMPOSE_FILE = REPO_ROOT / "infra" / "deploy" / "docker-compose.prod.yml"
DEFAULT_NGINX_CONF = REPO_ROOT / "infra" / "deploy" / "nginx-origin.conf.example"
DEFAULT_ORIGIN = "https://www.ddp.online"

_MIN_SECRET_LEN = 32
_PLACEHOLDER_SECRETS = frozenset(
    {"", "replace-me-in-production", "change-me", "changeme", "secret", "jwt-secret"}
)
_PLACEHOLDER_S3 = frozenset(
    {
        "",
        "minioadmin",
        "changeme",
        "change-me",
        "change_me",
        "replace-me-in-production",
        "secret",
        "access-key",
        "secret-key",
        "admin",
    }
)
_PLACEHOLDER_DB_PW = frozenset(
    {"", "ddp", "postgres", "changeme", "change-me", "secret"}
)
_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_SSRF_KEYS = (
    "DDP_TRAINING_ADAPTER_ALLOW_PRIVATE",
    "DDP_MODEL_ADAPTER_ALLOW_PRIVATE",
    "DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE",
)
_EXPECTED_WORKERS = (
    "worker",
    "datasource-worker",
    "ingestion-job-worker",
    "processing-worker",
    "export-worker",
    "quality-worker",
    "domain-worker",
    "usage-snapshot-worker",
    "webhook-outbound-worker",
    "maintenance-worker",
)
_ANGLE_RE = re.compile(r"<[A-Z0-9_]+>")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    category: str


@dataclass(frozen=True)
class HttpProbeResponse:
    status_code: int
    headers: dict[str, str]
    text: str

    def header(self, name: str) -> str:
        target = name.lower()
        for k, v in self.headers.items():
            if k.lower() == target:
                return v
        return ""


HttpRequester = Callable[[str, str, dict[str, str], float], HttpProbeResponse]


def _res(name: str, ok: bool, ok_msg: str, fail_msg: str, cat: str) -> CheckResult:
    return CheckResult(name, ok, ok_msg if ok else fail_msg, cat)


def _is_truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _has_placeholder(raw: str) -> bool:
    return bool(_ANGLE_RE.search(raw))


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env 文件，保留原始值（仅剥离外层对称引号）。"""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k = key.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
            v = v[1:-1]
        out[k] = v
    return out


def _check_jwt_env(env: dict[str, str]) -> list[CheckResult]:
    sec = env.get("DDP_JWT_SECRET", "")
    s = sec.strip()
    sec_ok = (
        sec == s
        and s.lower() not in _PLACEHOLDER_SECRETS
        and not _has_placeholder(s)
        and len(s) >= _MIN_SECRET_LEN
    )
    iss_ok = bool(
        env.get("DDP_JWT_ISSUER", "").strip()
        and env.get("DDP_JWT_AUDIENCE", "").strip()
    )
    return [
        _res(
            "env.jwt_secret",
            sec_ok,
            "DDP_JWT_SECRET 合规",
            "DDP_JWT_SECRET 非法或过短",
            "env",
        ),
        _res(
            "env.jwt_issuer_audience",
            iss_ok,
            "JWT issuer/audience 已配置",
            "DDP_JWT_ISSUER 或 DDP_JWT_AUDIENCE 为空",
            "env",
        ),
    ]


def _check_s3_and_db_env(env: dict[str, str]) -> list[CheckResult]:
    ak, sk = env.get("DDP_S3_ACCESS_KEY", ""), env.get("DDP_S3_SECRET_KEY", "")
    ak_s, sk_s = ak.strip(), sk.strip()
    errs: list[str] = []
    if ak_s.lower() in _PLACEHOLDER_S3 or _has_placeholder(ak_s):
        errs.append("DDP_S3_ACCESS_KEY 为空或占位值")
    if sk_s.lower() in _PLACEHOLDER_S3 or _has_placeholder(sk_s):
        errs.append("DDP_S3_SECRET_KEY 为空或占位值")
    if ak != ak_s or sk != sk_s:
        errs.append("S3 凭据含首尾空白")
    if ak_s and ak_s == sk_s:
        errs.append("AK 与 SK 不得相同")
    out = [
        _res("env.s3_credentials", not errs, "S3 生产凭据合规", "；".join(errs), "env")
    ]
    if "DDP_DB_PASSWORD" in env:
        pw = env["DDP_DB_PASSWORD"].strip()
        pw_ok = pw.lower() not in _PLACEHOLDER_DB_PW and not _has_placeholder(pw)
        out.append(
            _res(
                "env.db_password",
                pw_ok,
                "DB 口令已加固",
                "DDP_DB_PASSWORD 为默认/占位值",
                "env",
            )
        )
    return out


def _check_cors_env(env: dict[str, str]) -> CheckResult:
    raw = env.get("DDP_CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return CheckResult(
            "env.cors_origins", False, "未配置 DDP_CORS_ALLOWED_ORIGINS", "env"
        )
    try:
        origins = json.loads(raw)
    except json.JSONDecodeError:
        return CheckResult(
            "env.cors_origins", False, "CORS 配置不是合法 JSON 数组", "env"
        )
    if (
        not isinstance(origins, list)
        or not origins
        or not all(isinstance(x, str) for x in origins)
    ):
        return CheckResult(
            "env.cors_origins", False, "CORS 必须为非空字符串数组", "env"
        )
    errs: list[str] = []
    for item in origins:
        if item != item.strip():
            errs.append(f"含首尾空白: {item!r}")
        elif item == "*":
            errs.append("生产环境禁止通配符 *")
        else:
            p = urlsplit(item)
            if (
                p.scheme not in {"http", "https"}
                or not p.netloc
                or p.path
                or p.query
                or p.fragment
            ):
                errs.append(f"格式非法(不得含路径/尾斜杠): {item!r}")
    if len(set(origins)) != len(origins):
        errs.append("存在重复来源")
    return _res(
        "env.cors_origins",
        not errs,
        f"CORS 合规 ({len(origins)} 项)",
        "；".join(errs),
        "env",
    )


def _validate_redis_url(raw: str) -> str | None:
    if not raw.strip():
        return "启用 redis 限流必须配置 DDP_RATE_LIMIT_REDIS_URL"
    if raw != raw.strip():
        return "DDP_RATE_LIMIT_REDIS_URL 含首尾空白"
    try:
        p = urlsplit(raw)
        port = p.port
    except ValueError:
        return "DDP_RATE_LIMIT_REDIS_URL 格式或端口非法"
    if p.scheme not in _REDIS_SCHEMES:
        return f"协议必须为 redis/rediss，当前 {p.scheme!r}"
    if not p.hostname:
        return "缺少主机名"
    if port is not None and not (0 < port < 65_536):
        return "端口越界"
    return None


def _check_redis_and_ops_env(env: dict[str, str]) -> list[CheckResult]:
    out: list[CheckResult] = []
    if env.get("DDP_RATE_LIMIT_BACKEND", "memory").strip().lower() == "redis":
        err = _validate_redis_url(env.get("DDP_RATE_LIMIT_REDIS_URL", ""))
        out.append(
            _res(
                "env.rate_limit_redis",
                err is None,
                "Redis 限流配置合规",
                err or "",
                "env",
            )
        )
    mt = env.get("DDP_METRICS_ACCESS_TOKEN", "")
    if mt:
        m_ok = (
            mt == mt.strip()
            and mt.lower() not in _PLACEHOLDER_SECRETS
            and not _has_placeholder(mt)
            and len(mt) >= _MIN_SECRET_LEN
        )
        out.append(
            _res(
                "env.metrics_token",
                m_ok,
                "Metrics Token 合规",
                "Metrics Token 为占位或过短",
                "env",
            )
        )
    if _is_truthy(env.get("DDP_HSTS_ENABLED")):
        age = env.get("DDP_HSTS_MAX_AGE_SECONDS", "").strip()
        out.append(
            _res(
                "env.hsts",
                age.isdigit() and int(age) > 0,
                f"HSTS max-age={age}",
                "HSTS max-age 非法",
                "env",
            )
        )
    if _is_truthy(env.get("DDP_MAINTENANCE_ENABLED")):
        bdir = env.get("DDP_BACKUP_DIR", "").strip()
        out.append(
            _res(
                "env.maintenance_backup_dir",
                bool(bdir),
                f"备份目录 {bdir}",
                "缺少 DDP_BACKUP_DIR",
                "env",
            )
        )
    bad_ssrf = [k for k in _SSRF_KEYS if _is_truthy(env.get(k))]
    out.append(
        _res(
            "env.ssrf_private_guards",
            not bad_ssrf,
            "出站 SSRF 私网例外均关闭",
            f"禁止开启私网放行: {', '.join(bad_ssrf)}",
            "env",
        )
    )
    return out


def verify_env_map(env: dict[str, str]) -> list[CheckResult]:
    """校验生产环境变量，对齐 config.py::validate_runtime_security。"""
    ddp_env = env.get("DDP_ENV", "").strip().lower()
    out = [
        _res(
            "env.ddp_env",
            bool(ddp_env) and ddp_env != "local",
            f"DDP_ENV={ddp_env}",
            "DDP_ENV 不得为空或 local",
            "env",
        ),
        _res(
            "env.local_header_auth",
            not _is_truthy(env.get("DDP_LOCAL_HEADER_AUTH")),
            "DDP_LOCAL_HEADER_AUTH 已关闭",
            "非本地环境禁止开启 DDP_LOCAL_HEADER_AUTH",
            "env",
        ),
    ]
    out.extend(_check_jwt_env(env))
    out.extend(_check_s3_and_db_env(env))
    out.append(_check_cors_env(env))
    out.extend(_check_redis_and_ops_env(env))
    return out


def _load_compose_yaml(path: Path) -> dict[str, Any]:
    """惰性导入 PyYAML 并兼容 Docker Compose !override 标签。"""
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    def _override(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return loader.construct_scalar(node)  # type: ignore[arg-type]

    _Loader.add_constructor("!override", _override)
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    if not isinstance(data, dict):
        raise ValueError("compose YAML 顶层必须为映射")
    return data


def _svc_env(cfg: dict[str, Any]) -> dict[str, str]:
    raw = cfg.get("environment")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {
            s.partition("=")[0].strip(): s.partition("=")[2].strip()
            for s in raw
            if isinstance(s, str) and "=" in s
        }
    return {}


def verify_compose_prod(compose_path: Path) -> list[CheckResult]:
    """校验 infra/deploy/docker-compose.prod.yml 关键覆盖项。"""
    if not compose_path.exists():
        return [
            CheckResult(
                "compose.exists", False, f"文件不存在: {compose_path}", "compose"
            )
        ]
    services = _load_compose_yaml(compose_path).get("services")
    if not isinstance(services, dict):
        return [CheckResult("compose.services", False, "缺少 services 定义", "compose")]

    out: list[CheckResult] = []
    for svc in ("postgres", "redis"):
        ports = (services.get(svc) or {}).get("ports")
        out.append(
            _res(
                f"compose.{svc}_no_public_ports",
                isinstance(ports, list) and not ports,
                f"{svc} 已隐藏公网端口",
                f"{svc} 必须 ports: !override []",
                "compose",
            )
        )
    for svc in ("api", "minio"):
        ports = (services.get(svc) or {}).get("ports")
        ok = (
            isinstance(ports, list)
            and bool(ports)
            and all(isinstance(p, str) and p.startswith("127.0.0.1:") for p in ports)
        )
        out.append(
            _res(
                f"compose.{svc}_loopback_only",
                ok,
                f"{svc} 仅绑定 127.0.0.1",
                f"{svc} 必须绑定 127.0.0.1",
                "compose",
            )
        )

    api_env = _svc_env(services.get("api") or {})
    api_s3 = api_env.get("DDP_S3_ENDPOINT", "")
    api_db = api_env.get("DDP_DATABASE_URL", "")
    out.append(
        _res(
            "compose.api_public_s3_endpoint",
            "https://" in api_s3,
            f"api S3={api_s3}",
            "api DDP_S3_ENDPOINT 须指向公网 HTTPS",
            "compose",
        )
    )
    out.append(
        _res(
            "compose.api_db_url_override",
            bool(api_db)
            and "ddp:ddp@" not in api_db
            and "${DDP_DB_PASSWORD}" in api_db,
            "api 已覆盖生产 DB 凭据",
            "api DB URL 须用 ${DDP_DB_PASSWORD}",
            "compose",
        )
    )
    bad_w = [
        w
        for w in _EXPECTED_WORKERS
        if _svc_env(services.get(w) or {}).get("DDP_S3_ENDPOINT") != "http://minio:9000"
    ]
    out.append(
        _res(
            "compose.workers_internal_s3",
            not bad_w,
            f"全部 {len(_EXPECTED_WORKERS)} 个 Worker 走内网 minio:9000",
            f"Worker 缺少内网 S3: {', '.join(bad_w)}",
            "compose",
        )
    )
    return out


def verify_nginx_conf(nginx_path: Path) -> list[CheckResult]:
    """校验 infra/deploy/nginx-origin.conf.example 配置完整性。"""
    if not nginx_path.exists():
        return [
            CheckResult("nginx.exists", False, f"文件不存在: {nginx_path}", "nginx")
        ]
    text = nginx_path.read_text(encoding="utf-8")
    specs = (
        (
            "nginx.api_upstream",
            "proxy_pass http://127.0.0.1:8000;" in text and "api.ddp.online" in text,
            "api.ddp.online -> 127.0.0.1:8000",
        ),
        (
            "nginx.s3_upstream",
            "proxy_pass http://127.0.0.1:9000;" in text and "s3.ddp.online" in text,
            "s3.ddp.online -> 127.0.0.1:9000",
        ),
        (
            "nginx.tls_443",
            text.count("listen 443 ssl") >= 2,
            "API 与 S3 均启用 443 TLS",
        ),
        (
            "nginx.s3_streaming_headers",
            all(
                t in text
                for t in (
                    "ignore_invalid_headers off;",
                    "proxy_buffering off;",
                    "proxy_request_buffering off;",
                    "proxy_set_header Host $http_host;",
                )
            ),
            "S3 关闭缓冲并透传原始 Host",
        ),
        (
            "nginx.s3_cors_preflight",
            all(
                t in text
                for t in (
                    "if ($request_method = OPTIONS)",
                    "Access-Control-Allow-Origin",
                    "Access-Control-Expose-Headers",
                    "ETag",
                    "return 204;",
                )
            ),
            "S3 OPTIONS 预检放行与 ETag 暴露",
        ),
        (
            "nginx.forwarded_headers",
            "X-Forwarded-For" in text and "X-Forwarded-Proto https;" in text,
            "转发 X-Forwarded-For / X-Forwarded-Proto",
        ),
    )
    return [CheckResult(name, ok, desc, "nginx") for name, ok, desc in specs]


def _default_http_requester(
    method: str, url: str, headers: dict[str, str], timeout: float
) -> HttpProbeResponse:
    """惰性导入 httpx（遵守 MEM-INC-030）。"""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"探针 URL 必须为合法 http/https 地址：{url!r}")
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        # nosemgrep: ddp-ssrf-unvalidated-url -- 运维 CLI 探针脚本，仅由运维命令行显式传入目标公网 URL 且已校验 scheme 并禁用重定向
        resp = client.request(method, url, headers=headers)
        return HttpProbeResponse(resp.status_code, dict(resp.headers), resp.text)


def _safe_req(
    requester: HttpRequester,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> tuple[HttpProbeResponse | None, str | None]:
    try:
        return requester(method, url, headers, timeout), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _probe_api(
    api_url: str, origin: str, requester: HttpRequester, timeout: float
) -> list[CheckResult]:
    base, out = api_url.rstrip("/"), []
    resp, err = _safe_req(requester, "GET", f"{base}/health", {}, timeout)
    if err or resp is None:
        out.append(
            CheckResult("online.api_health", False, f"请求失败: {err}", "online")
        )
    else:
        try:
            st = json.loads(resp.text).get("status")
        except json.JSONDecodeError:
            st = None
        out.append(
            CheckResult(
                "online.api_health",
                resp.status_code == 200 and st in {"ok", "healthy"},
                f"HTTP {resp.status_code}, status={st!r}",
                "online",
            )
        )

    cors_h = {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    resp, err = _safe_req(requester, "OPTIONS", f"{base}/api/v1/me", cors_h, timeout)
    if err or resp is None:
        out.append(CheckResult("online.api_cors", False, f"请求失败: {err}", "online"))
    else:
        acao = resp.header("access-control-allow-origin")
        out.append(
            CheckResult(
                "online.api_cors",
                resp.status_code in {200, 204} and acao == origin,
                f"HTTP {resp.status_code}, ACAO={acao!r}",
                "online",
            )
        )
    return out


def _probe_web(
    web_url: str, requester: HttpRequester, timeout: float
) -> list[CheckResult]:
    base, out = web_url.rstrip("/"), []
    for name, path in (
        ("online.web_index", "/"),
        ("online.web_spa_assets_fallback", "/assets"),
    ):
        resp, err = _safe_req(requester, "GET", f"{base}{path}", {}, timeout)
        if err or resp is None:
            out.append(CheckResult(name, False, f"请求失败: {err}", "online"))
        else:
            ct = resp.header("content-type").lower()
            out.append(
                CheckResult(
                    name,
                    resp.status_code == 200 and "text/html" in ct,
                    f"HTTP {resp.status_code}, Content-Type={ct!r}",
                    "online",
                )
            )
    return out


def _probe_s3(
    s3_url: str, origin: str, requester: HttpRequester, timeout: float
) -> list[CheckResult]:
    h = {
        "Origin": origin,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type",
    }
    resp, err = _safe_req(requester, "OPTIONS", f"{s3_url.rstrip('/')}/", h, timeout)
    if err or resp is None:
        return [
            CheckResult("online.s3_cors_options", False, f"请求失败: {err}", "online")
        ]
    acao = resp.header("access-control-allow-origin")
    methods = resp.header("access-control-allow-methods").upper()
    ok = resp.status_code in {200, 204} and acao in {origin, "*"} and "PUT" in methods
    return [
        CheckResult(
            "online.s3_cors_options",
            ok,
            f"HTTP {resp.status_code}, ACAO={acao!r}, Methods={methods!r}",
            "online",
        )
    ]


def verify_online_endpoints(
    *,
    api_url: str | None = None,
    s3_url: str | None = None,
    web_url: str | None = None,
    origin: str | None = None,
    timeout: float = 10.0,
    requester: HttpRequester | None = None,
) -> list[CheckResult]:
    """对公网 API / Web / S3 端点执行只读冒烟探测。"""
    resolved = (origin or web_url or DEFAULT_ORIGIN).rstrip("/")
    req = requester or _default_http_requester
    out: list[CheckResult] = []
    if api_url:
        out.extend(_probe_api(api_url, resolved, req, timeout))
    if web_url:
        out.extend(_probe_web(web_url, req, timeout))
    if s3_url:
        out.extend(_probe_s3(s3_url, resolved, req, timeout))
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DDP 公网部署上线前自检与公网冒烟验证")
    p.add_argument("--env-file", type=Path, default=None, help="生产 .env 文件路径")
    p.add_argument(
        "--compose-file",
        type=Path,
        default=DEFAULT_COMPOSE_FILE,
        help="compose 生产覆盖文件",
    )
    p.add_argument(
        "--nginx-conf", type=Path, default=DEFAULT_NGINX_CONF, help="Nginx 配置路径"
    )
    p.add_argument("--skip-env", action="store_true", help="跳过 .env 安全红线校验")
    p.add_argument("--skip-offline", action="store_true", help="跳过全部离线配置校验")
    p.add_argument("--api-url", default=None, help="公网 API 基址")
    p.add_argument("--s3-url", default=None, help="公网 S3 基址")
    p.add_argument("--web-url", default=None, help="公网前端基址")
    p.add_argument("--origin", default=None, help="CORS 预检 Origin")
    p.add_argument("--timeout", type=float, default=10.0, help="在线探测超时秒数")
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="输出 JSON 格式报告"
    )
    return p.parse_args(argv)


def run_checks(
    args: argparse.Namespace, *, requester: HttpRequester | None = None
) -> list[CheckResult]:
    out: list[CheckResult] = []
    if not args.skip_offline:
        if not args.skip_env:
            env_path = args.env_file or DEFAULT_ENV_FILE
            if not env_path.exists():
                out.append(
                    CheckResult(
                        "env.file_exists", False, f"未找到 .env: {env_path}", "env"
                    )
                )
            else:
                out.extend(verify_env_map(parse_env_file(env_path)))
        out.extend(verify_compose_prod(args.compose_file))
        out.extend(verify_nginx_conf(args.nginx_conf))
    if args.api_url or args.s3_url or args.web_url:
        out.extend(
            verify_online_endpoints(
                api_url=args.api_url,
                s3_url=args.s3_url,
                web_url=args.web_url,
                origin=args.origin,
                timeout=args.timeout,
                requester=requester,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_checks(args)
    failed = [r for r in results if not r.passed]
    if args.as_json:
        print(
            json.dumps(
                {
                    "ok": not failed,
                    "passed": len(results) - len(failed),
                    "failed": len(failed),
                    "checks": [asdict(r) for r in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for r in results:
            print(
                f"[{'PASS' if r.passed else 'FAIL'}] ({r.category}) {r.name}: {r.detail}"
            )
        print(
            f"Summary: {len(results) - len(failed)}/{len(results)} passed, {len(failed)} failed"
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
