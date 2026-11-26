"""LLM 训练导出 HTTP 路由（V52-037，契约 ``fragments/llm_export.yaml``）。

两个端点，都是**项目内**资源：

- ``POST /projects/{id}/llm-exports``（写权限）：把调用方给出的**已就绪**记录
  交给 ``ddp_data.llm_export`` 生成训练制品，返回摘要（文件名 / sha256 /
  字节数 / 行数 / 门禁剔除统计），**不回传大文件内容、不落库、不写对象存储**。
  同一输入重复调用结果字节一致。
- ``GET /projects/{id}/llm-exports/formats``（读权限）：可用格式与格式版本。

为什么不做成异步 Job：本批只覆盖「计算 + 摘要」，制品落对象存储与下载属于
后续接线；同步路径因此显式限制单次记录数（``MAX_LLM_EXPORT_RECORDS``），
不做无上限的长事务。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.annotation_common import ReadScope, WriteScope
from app.api.v1.llm_export_models import (
    LlmExportCreate,
    LlmExportFormatsOut,
    LlmExportSummaryOut,
)
from app.application.export.llm_dataset import format_llm_export, list_export_formats

router = APIRouter(prefix="/projects/{project_id}", tags=["llm-export"])


@router.post(
    "/llm-exports",
    response_model=LlmExportSummaryOut,
    summary="生成 SFT/DPO/PRM 训练导出摘要",
)
def create_llm_export(payload: LlmExportCreate, scope: WriteScope) -> LlmExportSummaryOut:
    summary = format_llm_export(
        payload.format,
        [record.model_dump(mode="python") for record in payload.records],
    )
    return LlmExportSummaryOut.model_validate(summary)


@router.get(
    "/llm-exports/formats",
    response_model=LlmExportFormatsOut,
    summary="可选训练导出格式",
)
def get_llm_export_formats(scope: ReadScope) -> LlmExportFormatsOut:
    return LlmExportFormatsOut.model_validate({"formats": list_export_formats()})
