"""组合根：把应用端口接到具体基础设施适配器。

存在的理由：同一批用例（Episode、导入物化）既被 HTTP 路由使用，也被 Worker
使用。装配函数放在 ``api`` 会让 Worker 反向依赖 HTTP 层；放在 ``application``
会让应用层依赖 ``infrastructure``。组合根是唯一允许同时认识两侧的地方。
"""
