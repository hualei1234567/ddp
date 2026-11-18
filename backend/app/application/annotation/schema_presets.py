"""Schema 预设目录用例（只读）。

目录是静态代码资产：不读库、不写审计、不区分项目内容。项目级访问控制由
API 依赖的 ``ProjectScope`` 强制；本用例只负责稳定输出与未知 key 的语义。
"""

from __future__ import annotations

from app.domain.annotation.schema_presets import (
    SchemaPreset,
    SchemaPresetRegistry,
    default_registry,
)


class SchemaPresetCatalog:
    """七类常用 Schema 预设模板的只读目录。"""

    def __init__(self, registry: SchemaPresetRegistry | None = None) -> None:
        # 显式判断 None：空注册表也是合法输入，不能被 `or` 悄悄换成默认目录。
        self._registry = default_registry() if registry is None else registry

    @property
    def version(self) -> str:
        return self._registry.version

    def list(self) -> tuple[SchemaPreset, ...]:
        return self._registry.list_presets()

    def get(self, preset_key: str) -> SchemaPreset:
        return self._registry.get(preset_key)


__all__ = ["SchemaPresetCatalog"]
