"""数据源导入域的事件类型常量。

单独成模块的理由：``datasource.import.<action>`` 有两个产生点
（``use_cases.apply_import_action`` 的 API 路径与 ``import_runner`` 的 Worker 路径），
动作→事件的映射只在 ``domain/datasources/state_machines`` 的动作白名单旁定义一次，
两处共用，避免复制规则后各改一半。

R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
"""

from __future__ import annotations

# 键集合覆盖 ``domain/datasources/state_machines`` 的**全部**动作（客户端 + 系统）：
# 「``import_target_status`` 接受什么，这里就必须有什么」，新增动作时两处同步。
# 客户端动作受 ``validate_import_action`` 约束（须与契约 ImportAction 一致）；
# 系统动作（``retry``）不经该函数，只由 Worker 触发。
DATASOURCE_IMPORT_ACTION_EVENTS: dict[str, str] = {
    "start": "datasource.import.start",
    "succeed": "datasource.import.succeed",
    "fail": "datasource.import.fail",
    "cancel": "datasource.import.cancel",
    # 系统动作：Worker 发现可恢复失败时把任务从 FAILED 退回 PENDING。
    # 它不在契约 ImportAction 枚举里（客户端触发不了），但事件照样要归类留痕。
    "retry": "datasource.import.retry",
}
