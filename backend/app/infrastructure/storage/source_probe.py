"""数据集来源可达性探测适配器。

只做 HEAD 级别存在性检查，不下载内容；在领域巡检 Worker 中调用，
不进入 HTTP 线程。

语义与 ``process_objects`` 一致：对象不存在是稳定结论（``MISSING``），
存储不可用/超时/鉴权失败是"无法判定"（``UNREACHABLE``），两者绝不合并。
探测使用独立的短超时客户端并关闭 SDK 自动重试——重试策略由应用层
（``DatasetValiditySweep``）统一控制，避免一次巡检被 SDK 默认重试拖长。
"""

from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.application.datasets.ports import SourceReachability
from app.core.config import Settings
from app.infrastructure.storage.s3 import is_missing_object


class S3SourceProbe:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        timeout = settings.dataset_source_probe_timeout_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 0},
            ),
        )

    def probe(self, object_key: str) -> SourceReachability:
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if is_missing_object(exc):
                return SourceReachability.MISSING
            return SourceReachability.UNREACHABLE
        except BotoCoreError:
            return SourceReachability.UNREACHABLE
        return SourceReachability.REACHABLE


__all__ = ["S3SourceProbe"]
