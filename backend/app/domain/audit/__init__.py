"""审计日志查询域（只读）。

审计日志是「谁在什么时候对什么做了什么」的权威留痕，本域只提供**读**模型：
过滤条件值对象与结果枚举。写入路径在 ``app.application.audit``（各业务用例
同事务调用），本模块不参与写入，也不提供任何修改或删除规则——审计可追溯的
前提是记录不可篡改。
"""

from __future__ import annotations

from app.domain.audit.models import AuditQuery, AuditResult, normalize_filter_text

__all__ = ["AuditQuery", "AuditResult", "normalize_filter_text"]
