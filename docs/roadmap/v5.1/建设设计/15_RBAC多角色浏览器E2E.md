# 15｜RBAC 多角色浏览器 E2E

## 1. 目标
FR-044 当前 PARTIAL。V5.1 不新建权限系统，只补浏览器真实路径证明。

## 2. 角色矩阵
Collector：只看授权项目和分配的采集任务；不能改设备全局配置或查看其他项目数据。  
Annotator：只编辑有效 Assignment；不能修改他人 Submission 或审核无权任务。  
Reviewer：只审核授权项目；不能绕过状态机直接改结果。  
Dataset Reader：只读有权限 Dataset；不能冻结、删除或导出未授权版本。  
Project Admin：管理本项目用户和资源；不能跨项目越权。  
Platform Admin：执行平台级操作，但高危动作需要审计。  
以仓库现有角色名和 ROLE_PERMISSIONS 为准，不擅自增加角色。

## 3. E2E 场景
登录后菜单和列表范围正确；直接访问未授权 URL 被拒绝；直接调用 API 返回 403/404（按现有安全策略）；修改 URL 中 project_id 不可跨项目读取；下载文件 URL 不能越权；Annotation ID / DatasetVersion ID 猜测不可越权；Collector 不能执行 Reviewer 操作；Annotator 不能调用管理接口；Reviewer 不能审核未授权项目；Session 过期后敏感操作失败。

## 4. 测试数据隔离
至少两个 Project、多个角色和不同资源 ID。每个用例验证 UI 行为和服务端拒绝；不能只验证按钮隐藏。

## 5. 验收
浏览器 E2E 在 CI 可重复运行；跨项目和跨角色拒绝通过；错误响应不泄露不必要敏感信息；测试账号和种子数据可清理。
