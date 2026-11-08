"""身份与项目上下文领域异常。"""

from __future__ import annotations


class CrossProjectAccessError(ValueError):
    """检测到资源与当前项目上下文不一致，即潜在 IDOR / 跨项目访问。"""
