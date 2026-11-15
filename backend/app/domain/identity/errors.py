"""身份与项目上下文领域异常。"""

from __future__ import annotations


class CrossProjectAccessError(ValueError):
    """检测到资源与当前项目上下文不一致，即潜在 IDOR / 跨项目访问。

    ``project_scope_denied`` 与泛化的 ``forbidden`` 必须区分：前者是**安全信号**
    （有人在尝试跨项目访问不属于自己的资源，通常意味着 ID 枚举或客户端 bug），
    应当单独告警与计数；后者只是"这个账号没这个权限"，是正常业务结果。
    两者混用会让真正的越权探测淹没在普通的权限拒绝里。
    """

    code = "project_scope_denied"
