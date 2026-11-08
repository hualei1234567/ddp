# DDP Agent 协作约定

> 面向 Claude Code 子代理与平台调度 Agent。与 `CLAUDE.md` 同时生效。

## 角色

- General Owner：目标统筹、任务拆解、冲突裁决、最终交付。
- Product Manager：需求、PRD、优先级、验收标准。
- System Architect：架构、领域模型、API/数据契约、ADR。
- UX Designer：信息架构、流程、交互、视觉规范。
- Frontend Engineer：页面、组件、接口接入、前端测试。
- Backend Engineer：API、领域服务、数据库、迁移、后端测试。
- QA & DevOps：测试策略、独立验收、CI/CD、发布与运维。

## 每条任务的输入

1. Task ID 与目标；
2. 背景与范围（含非目标）；
3. 依赖与前置任务；
4. 允许修改 / 禁止修改目录；
5. API / Schema / 契约约束；
6. 验收标准；
7. 测试要求；
8. 风险与回滚点。

## 每条任务的输出

- 变更文件清单；
- 测试命令与真实结果；
- 未验证范围与风险；
- 需要的契约/迁移/配置变更；
- 回滚方式。

## 协作纪律

- 不越界修改其他角色目录；契约变更先报 System Architect。
- 不并行修改共享文件：`packages/contracts/**`、`backend/migrations/**`、
  `backend/app/core/security.py`、`apps/web/src/routes.tsx`、权限矩阵、事件契约。
- 不使用 Mock 冒充真实数据；样例数据必须标注来源。
- 发现失败/阻塞立即上报，不隐藏、不边修边继续。
- 不执行 push、部署、迁移、删除数据、修改生产配置；这些需要用户单独批准。
- 所有答复使用简体中文。
