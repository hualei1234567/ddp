"""按 Adapter 注册表解析导入元数据（N2 接线）。

**为什么单独一层**

导入用例（``ImportRunner``）此前由调用方直接注入 ``parse_episodes``——即
"用哪个解析器"是硬编码在 worker / e2e 脚本里的。Adapter 注册表有了之后，
解析器应当由**注册表决定**：按内容探测或按显式 ID 选择，并留下"用了哪个
Adapter、哪个版本、缺了哪些字段"的记录。

这一层只做选择与留痕，不实现解析；解析仍是各 Adapter 的既有实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from ddp_data.adapters import (
    AdapterNotFoundError,
    AdapterRegistry,
    AdapterSpec,
    build_default_registry,
)
from ddp_data.adapters.builtin import GenericJsonlParseError

from app.domain.datasources.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class ResolvedAdapter:
    """一次解析实际使用的 Adapter 及其声明。"""

    spec: AdapterSpec
    episodes: tuple[object, ...]


def resolve_and_parse(
    data: bytes,
    *,
    adapter_id: str | None = None,
    registry: AdapterRegistry | None = None,
) -> ResolvedAdapter:
    """解析元数据并返回所用 Adapter。

    ``adapter_id`` 显式指定时按 ID 取；未指定时按内容探测。两者都失配
    显式失败——不静默跳过，否则导入会"成功"却没有数据。
    """
    # 必须用 `is None` 而不是 `or`：AdapterRegistry 实现了 __len__，空注册表
    # 是 falsy，`registry or default` 会把调用方显式传入的空注册表偷偷换成
    # 默认注册表——测试隔离失效，且"注册表为空"这种真实配置错误被掩盖。
    active = build_default_registry() if registry is None else registry
    if adapter_id is not None:
        try:
            spec = active.get(adapter_id)
        except AdapterNotFoundError as exc:
            raise DomainValidationError(str(exc)) from exc
    else:
        detected = active.detect(data)
        if detected is None:
            raise DomainValidationError(
                "无法从内容判断数据格式；请显式指定 adapter_id。"
                f"已注册：{[item.adapter_id for item in active.list_specs()]}"
            )
        spec = detected
    try:
        episodes = spec.parser(data)
    except GenericJsonlParseError as exc:
        raise DomainValidationError(f"Adapter {spec.adapter_id} 解析失败：{exc}") from exc
    if not episodes:
        raise DomainValidationError(f"Adapter {spec.adapter_id} 未产出任何 Episode")
    return ResolvedAdapter(spec=spec, episodes=tuple(episodes))


__all__ = ["ResolvedAdapter", "resolve_and_parse"]
