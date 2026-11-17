"""数据源域命令 DTO。

命令只承载"用户想要什么"；许可证规范化、gated 一致性这些规则在领域层。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewDataSource:
    code: str
    name: str
    repo_id: str
    revision: str
    license: str
    access_condition: str
    is_gated: bool = False
    kind: str = "HUGGINGFACE"
    config_name: str | None = None
    split: str | None = None
    dataset_card_url: str | None = None
    notes: str | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class DataSourcePatch:
    name: str | None = None
    license: str | None = None
    access_condition: str | None = None
    is_gated: bool | None = None
    config_name: str | None = None
    split: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NewDataSourceImport:
    scope: str
    tool_name: str
    tool_version: str
    sample_limit: int | None = None
    cache_location: str | None = None
    # 导入数据的归属：数据是"为哪个采集任务/批次采的"。为空表示不归属任何
    # 任务（例如公共数据集的一次性拉取）。导入产出的 RawAsset 会继承这两个值，
    # 物化出的 Episode 同样带上 task_id——否则下游按任务统计永远查不到。
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
