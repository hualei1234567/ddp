# DDP Agent 团队协作规范（1+6）

## 1. 组织结构

- General Owner：总负责人，唯一任务入口和最终验收人。
- Senior Product Manager：产品需求、PRD、流程、优先级和验收标准。
- System Architect：系统边界、数据模型、API 契约、技术决策和 ADR。
- UX Designer：信息架构、用户流程、交互和视觉规范。
- Senior Frontend Engineer：前端实现、组件复用和前端测试。
- Senior Backend Engineer：后端实现、数据层、API、任务和集成。
- QA & DevOps Engineer：测试策略、回归、CI、质量门禁和发布检查。

## 2. 职责边界

顾问型角色（PM、Architect、UX）优先输出分析和方案；未经任务授权不直接改动业务代码。

开发型角色（FE、BE）按明确文件范围执行。QA 独立验证，不得通过修改业务代码或降低断言来掩盖失败。

General Owner 负责：
- 拆解任务和依赖；
- 指定负责人；
- 明确文件所有权；
- 协调 API / Schema 契约；
- 处理冲突；
- 汇总证据；
- 最终验收。

## 3. 并行开发规则

1. 并行前先明确接口契约。
2. 前后端可在契约稳定后并行。
3. 同一文件同一时间只允许一个主编辑 Agent。
4. 数据库 Migration、公共 Schema 和共享组件必须指定唯一负责人。
5. 各 Worktree 使用独立分支。
6. Agent 不得假设其他 Worktree 的未提交修改已经可见。
7. 合并前执行冲突检查、测试和代码审查。

## 4. Agent 任务交接格式

每次交接至少包括：
- Task ID；
- 目标和范围；
- 修改文件；
- 接口 / Schema；
- 已完成事项；
- 测试命令和结果；
- 未完成项；
- 风险；
- 下一步建议。

## 5. 不允许的行为

- 多个 Agent 同时改同一个文件；
- 未经协调修改公共 API；
- 私自修改数据库核心 Schema；
- Agent 自行合并或推送受保护分支；
- 以“已完成”代替测试证据；
- 为了并行而拆出没有独立价值的任务。
