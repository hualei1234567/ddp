"""读取处理运行的元数据产物文档（V31-018）。

元数据阶段把每个输入对象的探测结果写成 JSON 产物，产物目录（V31-016）记下
``object_key`` 与 ``source_ref``。本适配器把两者接起来：给一组原始对象 key，
返回 ``{key: 文档}``。

为什么不复用 :class:`TimeAlignmentHandler` 的读取：那一个是"读一个指定的
manifest 并严格校验 schema"，这里是"按运行列出产物并尽量都读出来"。后者的
失败语义是**跳过**（文档缺失会由边界推导显式记成 ``metadata_artifact_missing``），
前者的失败语义是阶段失败——把两者塞进一个函数会让"缺一个文档"到底是
"没产出"还是"读坏了"变得不可区分。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

from app.application.processing.ports import (
    ObjectAccessError,
    ProcessingArtifactRepository,
    StageArtifactStore,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifactKind


class ProcessingMetadataDocumentReader:
    """按运行列出 METADATA 产物并读出文档；读不出的键不出现。"""

    def __init__(
        self,
        artifacts: ProcessingArtifactRepository,
        store: StageArtifactStore,
        *,
        max_document_bytes: int,
    ) -> None:
        self._artifacts = artifacts
        self._store = store
        self._max_document_bytes = max_document_bytes

    def read_metadata_documents(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        source_refs: tuple[str, ...],
    ) -> Mapping[str, Mapping[str, object]]:
        wanted = set(source_refs)
        documents: dict[str, Mapping[str, object]] = {}
        for artifact in self._artifacts.list(
            scope,
            run_id=run_id,
            kind=ProcessingArtifactKind.METADATA.value,
        ):
            source_ref = artifact.source_ref
            # 只关心本次运行声明的输入；产物目录可能同时含历史尝试的行。
            if source_ref is None or source_ref not in wanted:
                continue
            if source_ref in documents:
                continue
            document = self._read_document(artifact.object_key)
            if document is not None:
                documents[source_ref] = document
        return documents

    def _read_document(self, object_key: str) -> Mapping[str, object] | None:
        try:
            raw = self._store.read_bytes(object_key, max_bytes=self._max_document_bytes)
        except ObjectAccessError:
            # 产物读不出来不是"边界不存在"，交给上层按 metadata_artifact_missing
            # 记录；把它变成阶段失败会让一次瞬时存储抖动毁掉整个运行。
            return None
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None


__all__ = ["ProcessingMetadataDocumentReader"]
