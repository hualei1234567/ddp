"""多基地资源只读视图的读模型（N9-005）。

``SiteOverviewEntry`` 把**同一份** ``Location`` 与派生计数组合在一起，只用于
HTTP 响应组装。它不落库、不参与状态机，也不反向修改场地实体：

- 计数来自 ``SiteResourceSummaryRepository``（SQL 聚合下推）；
- 场地本身仍由 ``LocationRepository`` 提供，字段语义不做二次解释。

单独成模块而不是塞进 ``use_cases.py``：读模型的形状会被路由与测试直接依赖，
与用例编排的关注点不同。这里没有业务规则，只有稳定结构。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.locations.models import Location, LocationResourceCounts


@dataclass(frozen=True, slots=True)
class SiteOverviewEntry:
    """一个场地的资源概览；``resources`` 全 0 表示该基地当前没有资源。"""

    location: Location
    resources: LocationResourceCounts


__all__ = ["SiteOverviewEntry"]
