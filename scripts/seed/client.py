"""种子脚本共用的 HTTP 客户端与断言。"""

from __future__ import annotations

import datetime
import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


class SeedFailure(RuntimeError):
    """某一步不符合契约。消息里带状态码与响应体，便于直接定位。"""


@dataclass
class Context:
    """一次种子运行共享的上下文。"""

    base_url: str
    token: str
    project_id: str
    user_id: str
    stamp: str
    # 各模块产出的 ID，供后续模块引用（如标注任务要用 Episode）
    ids: dict[str, dict[str, str]] = field(default_factory=dict)
    # 多身份：某些动作有职责分离要求（如采集任务创建人不得审批自己的任务），
    # 种子必须能以第二个人的身份操作。键是角色名，值是 Bearer 令牌。
    tokens: dict[str, str] = field(default_factory=dict)

    def as_(self, role: str) -> Client:
        try:
            token = self.tokens[role]
        except KeyError as exc:
            raise SeedFailure(f"缺少身份：{role}") from exc
        return Client(self.base_url, token)

    def remember(self, group: str, key: str, value: str) -> str:
        self.ids.setdefault(group, {})[key] = value
        return value

    def recall(self, group: str, key: str) -> str:
        try:
            return self.ids[group][key]
        except KeyError as exc:
            raise SeedFailure(f"缺少前置数据：{group}.{key}") from exc


class Client:
    """极简 HTTP 客户端：只做本仓库需要的 JSON 请求。"""

    def __init__(self, base_url: str, token: str, *, max_retries: int = 6) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._max_retries = max_retries

    def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, Any]:
        data = raw
        merged = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            **(headers or {}),
        }
        if body is not None:
            data = json.dumps(body).encode()
            merged["Content-Type"] = "application/json"
        # 只连本地 API（http://127.0.0.1:8000）；scheme 由调用方传入的
        # base_url 决定，脚本不做任何 URL 拼接以外的处理。
        request = urllib.request.Request(  # noqa: S310
            self._base + path, data=data, method=method, headers=merged
        )
        # 写限流是按客户端 IP 的滑动窗口（默认 120 次/分钟）。种子脚本一次
        # 运行会发几百个写请求，撞上 429 很正常——按 Retry-After 退避重试，
        # 而不是把 429 当成业务失败。
        for attempt in range(self._max_retries + 1):
            status, payload, retry_after = self._once(request)
            if status != 429 or attempt == self._max_retries:
                return status, payload
            time.sleep(retry_after or min(2**attempt, 15))
        return status, payload  # pragma: no cover - 循环必然在上面返回

    def _once(self, request: urllib.request.Request) -> tuple[int, Any, float]:
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None), 0.0
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                parsed = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                parsed = payload.decode("utf-8", "replace")
            retry_after = 0.0
            header = exc.headers.get("Retry-After") if exc.headers else None
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = 0.0
            return exc.code, parsed, retry_after


def expect(
    label: str,
    result: tuple[int, Any],
    want: int | tuple[int, ...],
    *,
    allow: tuple[int, ...] = (),
) -> Any:
    """断言状态码；``allow`` 用于「可能已存在」等幂等场景。"""
    status, payload = result
    accepted = (want if isinstance(want, tuple) else (want,)) + allow
    if status in accepted:
        return payload
    raise SeedFailure(
        f"{label}：期望 {accepted}，实际 {status}\n"
        f"  响应：{json.dumps(payload, ensure_ascii=False)[:600]}"
    )


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def idempotency_key() -> str:
    return str(uuid.uuid4())


def if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}
