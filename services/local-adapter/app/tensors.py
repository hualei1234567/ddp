"""像素字节 → sklearn 输入矩阵（0-1 归一化）。训练与推理共用。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.errors import AdapterError


def to_matrix(features: Sequence[bytes]) -> Any:
    import numpy as np

    if not features:
        raise AdapterError("no_features", "没有可用的帧特征")
    rows = [np.frombuffer(payload, dtype=np.uint8) for payload in features]
    sizes = {row.size for row in rows}
    if len(sizes) != 1:
        raise AdapterError("feature_size_mismatch", "帧尺寸不一致，拒绝拼接矩阵")
    return np.stack(rows).astype(np.float32) / 255.0


__all__ = ["to_matrix"]
