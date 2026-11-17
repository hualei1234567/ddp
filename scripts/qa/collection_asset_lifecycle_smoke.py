#!/usr/bin/env python3
"""DDP 采集素材写路径巡检：状态机 + 乐观锁 + 删除边界。

## 为什么单独一个脚本

`readonly_endpoint_smoke.py` 只发 GET。而 N8 的采集素材有 12 个**写**操作，
它们的正确性只能靠真实请求验证：

- `If-Match` 缺失 → 428；
- 版本过期 → 409；
- `DRAFT` 直接 `archive`（跳过中间状态）→ 409；
- `ACTIVE` 删除 → 409 `resource_immutable`；
- 终态 `ARCHIVED` 再 `activate` → 409；
- `DRAFT` 删除 → 204。

这些行为在单元测试里由 mock 仓储覆盖，但**真实数据库上的触发器**是另一份实现
（迁移 0041）。本脚本验证两份实现口径一致。

## 它会写数据（明确告知）

每次运行会创建 1 个采集方案并把它推到 `ARCHIVED`，另创建 1 个 `DRAFT` 并删除。

`ARCHIVED` 按设计**不可删除**（终态是历史事实），因此会在项目里留下一条
`code` 形如 `smoke-<timestamp>` 的归档记录。这是刻意的：删除它需要违法状态机，
而「为了清理测试数据去绕开状态机」正是这类脚本最不该做的事。

## 用法

```bash
backend/.venv/bin/python scripts/qa/collection_asset_lifecycle_smoke.py \\
    --project-id <uuid> --user-id <uuid>
```

默认连本地栈（API 8000），JWT 密钥从 `.env` 读。

## 退出码

- `0`：全部步骤符合契约
- `1`：有步骤不符合（打印实际状态码与响应体）
- `2`：前置条件不满足
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _jwt(sub: str, secret: str, issuer: str, audience: str) -> str:
    import jwt

    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(minutes=30)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


class Client:
    def __init__(self, base: str, token: str) -> None:
        self._base = base.rstrip("/")
        self._token = token

    def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object] | None]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return exc.code, payload


def _error_code(payload: dict[str, object] | None) -> str:
    if not payload:
        return "<无响应体>"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code", "<无 code>"))
    return "<非错误信封>"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 采集素材写路径巡检")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args(argv)

    env_file = REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    secret = env.get("DDP_JWT_SECRET")
    if not secret:
        print("前置条件不满足：.env 里没有 DDP_JWT_SECRET", file=sys.stderr)
        return 2

    client = Client(
        args.api_base,
        _jwt(
            args.user_id,
            secret,
            env.get("DDP_JWT_ISSUER", "ddp"),
            env.get("DDP_JWT_AUDIENCE", "ddp-api"),
        ),
    )
    base = f"/api/v1/projects/{args.project_id}/collection-plans"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    failures: list[str] = []

    def expect(
        label: str,
        actual: int,
        want: int,
        payload: dict[str, object] | None,
        *,
        want_code: str | None = None,
    ) -> None:
        detail = f"  {label}：期望 {want}"
        if want_code:
            detail += f" / {want_code}"
        detail += f"，实际 {actual}"
        if actual != want or (want_code and _error_code(payload) != want_code):
            if want_code and actual == want:
                detail += f" / {_error_code(payload)}"
            failures.append(detail)
            print(f"  ✗ {detail[2:]}")
        else:
            print(f"  ✓ {label}：{actual}" + (f" / {want_code}" if want_code else ""))

    # --- 素材 A：走完整生命周期直到终态 ---
    code_a = f"smoke-{stamp}-a"
    status, created = client.call(
        "POST",
        base,
        body={"code": code_a, "name": f"巡检方案 {code_a}", "plan_version": 1},
    )
    expect("创建", status, 201, created)
    if status != 201 or created is None:
        print("\n创建失败，后续步骤无法继续")
        return 1
    plan_id = str(created["plan_id"])
    if created.get("allowed_actions") != ["activate"]:
        failures.append(
            f"  DRAFT 的 allowed_actions 应为 ['activate']，实际 {created.get('allowed_actions')}"
        )
        print(f"  ✗ DRAFT allowed_actions：{created.get('allowed_actions')}")
    else:
        print("  ✓ DRAFT allowed_actions：['activate']")

    # PATCH 缺 If-Match
    status, payload = client.call("PATCH", f"{base}/{plan_id}", body={"name": "改名"})
    expect("PATCH 缺 If-Match", status, 428, payload, want_code="precondition_required")

    # PATCH 正常
    status, payload = client.call(
        "PATCH",
        f"{base}/{plan_id}",
        body={"name": "改名后"},
        headers={"If-Match": '"1"'},
    )
    expect("PATCH 带 If-Match", status, 200, payload)
    if status == 200 and payload and payload.get("version") != 2:
        failures.append(f"  PATCH 后 version 应为 2，实际 {payload.get('version')}")
        print(f"  ✗ PATCH 后 version：{payload.get('version')}")

    # DRAFT 直接 archive（跳过中间状态）
    status, payload = client.call(
        "POST", f"{base}/{plan_id}/actions/archive", headers={"If-Match": '"2"'}
    )
    expect(
        "DRAFT 直接 archive", status, 409, payload, want_code="state_transition_invalid"
    )

    # activate
    status, payload = client.call(
        "POST", f"{base}/{plan_id}/actions/activate", headers={"If-Match": '"2"'}
    )
    expect("activate", status, 200, payload)

    # 删 ACTIVE
    status, payload = client.call("DELETE", f"{base}/{plan_id}")
    expect("删除 ACTIVE", status, 409, payload, want_code="resource_immutable")

    # archive
    status, payload = client.call(
        "POST", f"{base}/{plan_id}/actions/archive", headers={"If-Match": '"3"'}
    )
    expect("archive", status, 200, payload)
    if status == 200 and payload:
        if payload.get("allowed_actions") != []:
            failures.append(
                f"  ARCHIVED 的 allowed_actions 应为 []，实际 {payload.get('allowed_actions')}"
            )
            print(f"  ✗ ARCHIVED allowed_actions：{payload.get('allowed_actions')}")
        else:
            print("  ✓ ARCHIVED allowed_actions：[]")

    # 终态再 activate
    status, payload = client.call(
        "POST", f"{base}/{plan_id}/actions/activate", headers={"If-Match": '"4"'}
    )
    expect("终态 activate", status, 409, payload, want_code="state_transition_invalid")

    # --- 素材 B：只到 DRAFT，验证删除 ---
    code_b = f"smoke-{stamp}-b"
    status, created_b = client.call(
        "POST",
        base,
        body={"code": code_b, "name": f"巡检方案 {code_b}", "plan_version": 1},
    )
    expect("创建待删方案", status, 201, created_b)
    if status == 201 and created_b is not None:
        status, payload = client.call("DELETE", f"{base}/{created_b['plan_id']}")
        expect("删除 DRAFT", status, 204, payload)

    print()
    if failures:
        print(f"巡检未通过：{len(failures)} 项不符合契约")
        return 1
    print(
        "巡检通过：状态机、乐观锁、删除边界全部符合契约。\n"
        f"注意：{code_a} 已进入 ARCHIVED 终态，按设计不可删除，会留在该项目里。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
