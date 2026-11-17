#!/usr/bin/env python3
"""DDP 只读端点巡检：找出真实运行栈上的 5xx。

## 为什么需要它

单元测试与集成测试各自跑在自己的数据库、自己的装配上；**真实运行栈**（真实
迁移产物 + 真实数据 + 真实容器）里仍可能有测试覆盖不到的错误。本仓库已实际
发生过一次：`action_specs.actions` 在 ORM 里是 `jsonb`、在迁移里是 `json`，
于是 `jsonb_array_elements(json)` 只在真实库上报错，测试全绿、接口 500。

这个脚本不验证业务语义，只回答一个问题：**有没有端点在真实栈上炸了**。

## 它怎么找真实 ID

按路径参数名从数据库取一条真实记录（`robot_id` → `robots` 的第一行等）。
参数名在不同资源下重复时（`task_id` 既是采集任务也是标注任务）按参数前一段
路径判定表名，避免拿到别的表的 ID——那只会换来 404，而 404 不计入 5xx。
取不到就跳过该端点并计入「跳过」，而不是伪造一个 ID——伪造只会得到 404，
看起来像「端点没问题」，实际什么都没验证。

## 用法

```bash
# 默认：连本地 compose 栈（API 8000，PG 55432）
backend/.venv/bin/python scripts/qa/readonly_endpoint_smoke.py

# 指定项目与身份（需要该用户是该项目成员）
backend/.venv/bin/python scripts/qa/readonly_endpoint_smoke.py \\
    --project-id <uuid> --user-id <uuid>

# 只跑无路径参数的端点（不需要任何真实数据）
backend/.venv/bin/python scripts/qa/readonly_endpoint_smoke.py --simple-only
```

## 退出码

- `0`：无 5xx
- `1`：有 5xx（列出端点与响应体前 260 字符）
- `2`：前置条件不满足（契约读不到、连不上 API / 数据库）

## 它不做什么

- 不写任何数据（只发 GET）；
- 不判断 4xx 是否「正确」——403/404 可能是真实的权限或数据状态，
  本脚本只对 5xx 报警；
- 不替代测试。它是测试之外的一层网。
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# 路径参数名 → 取一条真实 ID 的 SQL。
# 只放**确定存在**的表；取不到时脚本会跳过并如实计数。
ID_SQL: dict[str, str] = {
    "robot_id": "select id from robots limit 1",
    "device_id": "select id from devices limit 1",
    "sensor_id": "select id from sensors limit 1",
    "calibration_id": "select id from calibrations limit 1",
    "profile_id": "select id from calibration_profiles limit 1",
    "location_id": "select id from locations limit 1",
    "plan_id": "select id from collection_plans limit 1",
    "sop_id": "select id from collection_sops limit 1",
    "scenario_id": "select id from scenarios limit 1",
    "spec_id": "select id from action_specs limit 1",
    "task_id": "select id from collection_tasks limit 1",
    "batch_id": "select id from collection_batches limit 1",
    "episode_id": "select id from episodes limit 1",
    "asset_id": "select id from raw_assets limit 1",
    "upload_id": "select id from upload_sessions limit 1",
    "job_id": "select id from ingestion_jobs limit 1",
    "import_id": "select id from data_source_imports limit 1",
    "data_source_id": "select id from data_sources limit 1",
    "dataset_id": "select id from datasets limit 1",
    "version_id": "select id from dataset_versions limit 1",
    "export_job_id": "select id from export_jobs limit 1",
    "task_id_annotation": "select id from annotation_tasks limit 1",
    "submission_id": "select id from annotation_submissions limit 1",
    "schema_id": "select id from annotation_schemas limit 1",
    "check_id": "select id from annotation_quality_checks limit 1",
    "gold_set_id": "select id from annotation_gold_sets limit 1",
    "suggestion_id": "select id from annotation_ai_suggestions limit 1",
    "requirement_id": "select id from data_requirements limit 1",
    "request_id": "select id from training_requests limit 1",
    "revision_id": "select id from training_request_revisions limit 1",
    "delivery_id": "select id from training_deliveries limit 1",
    "supplier_id": "select id from suppliers limit 1",
    "inbound_batch_id": "select id from inbound_batches limit 1",
    "model_id": "select id from models limit 1",
    "model_version_id": "select id from model_versions limit 1",
    "evaluation_id": "select id from evaluations limit 1",
    "feedback_id": "select id from model_feedback limit 1",
}

# 同一参数名在不同资源下指向**不同的表**：`task_id` 同时用于采集任务、标注任务、
# 推理任务，`policy_id` 分别用于去重/拆分/泄漏/质量四类数据集策略。只按参数名取值
# 会拿错表，而拿错的 ID 一般返回 404——404 不计入 5xx，**看起来就像巡检通过**。
# 因此优先用参数前一段路径判定表名，判不出来才退回上面的 ID_SQL。
SEGMENT_ID_SQL: dict[str, str] = {
    "annotation-tasks": "select id from annotation_tasks limit 1",
    "collection-tasks": "select id from collection_tasks limit 1",
    "inference-tasks": "select id from inference_tasks limit 1",
    "annotation-reviews": "select id from annotation_reviews limit 1",
    "annotation-arbitrations": "select id from annotation_arbitrations limit 1",
    "annotation-assignments": "select id from annotation_assignments limit 1",
    "annotation-sample-sets": "select id from annotation_sample_sets limit 1",
    "quality-samples": "select id from annotation_quality_samples limit 1",
    "iaa-reports": "select id from annotation_iaa_reports limit 1",
    "dedup-policies": "select id from dataset_dedup_policies limit 1",
    "split-policies": "select id from dataset_split_policies limit 1",
    "leakage-policies": "select id from dataset_leakage_policies limit 1",
    "quality-policies": "select id from dataset_quality_policies limit 1",
    "items": "select id from dataset_items limit 1",
    "segments": "select id from inbound_segments limit 1",
    "grants": "select id from dataset_grants limit 1",
    "external-training-references": "select id from external_training_references limit 1",
    "upload-sessions": "select id from upload_sessions limit 1",
    "inference-services": "select id from inference_services limit 1",
}

# 参数名本身不足以定位、必须整体解析的端点：审计日志的资源类型与资源 ID 必须来自
# **同一行**，否则组合出来必然是 404。
PAIR_ID_SQL: dict[str, tuple[str, str, str]] = {
    "/audit-logs/": (
        "select resource_type || '|' || resource_id from audit_logs"
        " where resource_type is not null and resource_id is not null limit 1",
        "resource_type",
        "resource_id",
    ),
}

# 不是行 ID 的参数：导出产物路径要求在对象存储里真实存在，这里不伪造。
NON_ID_PARAMS = frozenset({"artifact_path"})

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _load_contract() -> dict[str, Any]:
    import yaml

    path = REPO_ROOT / "packages" / "contracts" / "openapi.yaml"
    return yaml.safe_load(path.read_text())


def _iter_get_operations(
    contract: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    import yaml

    out: list[tuple[str, dict[str, Any]]] = []
    for path, item in contract["paths"].items():
        if not isinstance(item, dict) or "$ref" not in item:
            continue
        frag, pointer = item["$ref"].split("#/", 1)
        doc = yaml.safe_load(
            (REPO_ROOT / "packages" / "contracts" / frag[2:]).read_text()
        )
        node = doc[pointer]
        if "parameters" in node:
            # 路径级公共参数不参与方法判定
            pass
        if "get" in node:
            out.append((path, node["get"]))
    return out


def _psql(
    sql: str, *, host: str, port: int, user: str, password: str, db: str
) -> str | None:
    env = {"PGPASSWORD": password}
    try:
        result = subprocess.run(
            ["psql", "-h", host, "-p", str(port), "-U", user, "-d", db, "-tAc", sql],
            capture_output=True,
            text=True,
            timeout=10,
            env={**__import__("os").environ, **env},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    if not value or "ERROR" in value.upper():
        return None
    return value


def _jwt(sub: str, secret: str, issuer: str, audience: str) -> str:
    import jwt

    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(minutes=60)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


def _request(url: str, token: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(400).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"连接失败：{exc}"


class IdResolver:
    """把路径参数解析成一条**真实存在**的记录 ID。

    解析不到就返回 ``None``，调用方跳过该端点——伪造一个 ID 只会得到 404，
    看起来像「端点没问题」，实际什么都没验证到。
    """

    def __init__(self, pg: dict[str, Any]) -> None:
        self._pg = pg
        self._cache: dict[str, str | None] = {}

    def _query(self, sql: str) -> str | None:
        if sql not in self._cache:
            self._cache[sql] = _psql(sql, **self._pg)
        return self._cache[sql]

    def resolve(self, path: str, names: set[str]) -> dict[str, str] | None:
        if names & NON_ID_PARAMS:
            return None
        for prefix, (sql, left, right) in PAIR_ID_SQL.items():
            if prefix in path and names == {left, right}:
                raw = self._query(sql)
                value_left, separator, value_right = (raw or "").partition("|")
                if not separator or not value_left or not value_right:
                    return None
                return {left: value_left, right: value_right}
        segments = path.strip("/").split("/")
        resolved: dict[str, str] = {}
        for name in names:
            sql = None
            for index, segment in enumerate(segments):
                if segment != "{" + name + "}":
                    continue
                if index and segments[index - 1] in SEGMENT_ID_SQL:
                    sql = SEGMENT_ID_SQL[segments[index - 1]]
                break
            if sql is None:
                sql = ID_SQL.get(name)
            if sql is None:
                return None
            value = self._query(sql)
            if not value:
                return None
            resolved[name] = value
        return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 只读端点 5xx 巡检")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--pg-host", default="127.0.0.1")
    parser.add_argument("--pg-port", type=int, default=55432)
    parser.add_argument("--pg-user", default="ddp")
    parser.add_argument("--pg-password", default="ddp")
    parser.add_argument("--pg-db", default="ddp")
    parser.add_argument("--project-id", default=None, help="默认取 projects 第一行")
    parser.add_argument("--user-id", default=None, help="默认取该项目的第一名成员")
    parser.add_argument(
        "--simple-only", action="store_true", help="只跑无额外路径参数的端点"
    )
    args = parser.parse_args(argv)

    env_file = REPO_ROOT / ".env"
    env = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    secret = env.get("DDP_JWT_SECRET")
    if not secret:
        print("前置条件不满足：.env 里没有 DDP_JWT_SECRET", file=sys.stderr)
        return 2

    pg = {
        "host": args.pg_host,
        "port": args.pg_port,
        "user": args.pg_user,
        "password": args.pg_password,
        "db": args.pg_db,
    }
    project_id = args.project_id or _psql("select id from projects limit 1", **pg)
    if not project_id:
        print("前置条件不满足：取不到任何项目 ID", file=sys.stderr)
        return 2
    user_id = args.user_id or _psql(
        f"select user_id from project_members where project_id = '{project_id}' limit 1",
        **pg,
    )
    if not user_id:
        print("前置条件不满足：取不到该项目成员 ID", file=sys.stderr)
        return 2

    token = _jwt(
        user_id,
        secret,
        env.get("DDP_JWT_ISSUER", "ddp"),
        env.get("DDP_JWT_AUDIENCE", "ddp-api"),
    )

    contract = _load_contract()
    operations = _iter_get_operations(contract)
    resolver = IdResolver(pg)

    probed = 0
    skipped = 0
    failures: list[tuple[str, int, str]] = []
    for path, _op in sorted(operations):
        if not path.startswith("/api/v1/projects/{project_id}"):
            continue
        names = set(re.findall(r"\{(\w+)\}", path)) - {"project_id"}
        if args.simple_only and names:
            continue
        resolved = resolver.resolve(path, names)
        if resolved is None:
            skipped += 1
            continue
        url = args.api_base + path.replace("{project_id}", str(project_id))
        for name, value in resolved.items():
            url = url.replace("{" + name + "}", value)
        status, body = _request(url, token)
        probed += 1
        if status == 0 or status >= 500:
            failures.append((url, status, body))

    print(f"已探测 {probed} 个只读端点，跳过 {skipped} 个（缺真实 ID）")
    for url, status, body in failures:
        print(f"  [{status}] {url}")
        print(f"        {body[:260]}")
    if failures:
        print(f"\n巡检未通过：{len(failures)} 个端点返回 5xx 或连接失败")
        return 1
    print("巡检通过：全部只读端点无 5xx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
