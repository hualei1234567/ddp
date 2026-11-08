# DDP 协作规则（Claude Code / Agent 必读）

> 唯一建设基线：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`。
> 桌面 memory 仅供参考；与 `docs/` 冲突时，以 `docs/` 为准。
> 所有思考、工具说明、代码注释与最终答复使用简体中文。

## 先确认现场

- 开始前读本文件、`AGENTS.md`、相关 ADR 与任务卡。
- 检查 Git 状态、当前分支、是否有其他会话的未提交改动。
- 以实际代码、命令、测试和运行结果为准；历史报告不代表当前状态。
- 结论必须附证据：文件路径、命令、测试名、接口或迁移号。
- 不确定就标 `[未验证]`；未执行标 `NOT RUN`；环境阻塞标 `BLOCKED`。

## 代码边界

- `backend/app/api`：HTTP 参数、响应、鉴权适配；不承载权威业务规则。
- `backend/app/application`：用例编排、事务边界、DTO 转换。
- `backend/app/domain`：实体、值对象、状态机、领域规则；不依赖 Web/ORM/队列/云 SDK。
- `backend/app/infrastructure`：Repository、对象存储、队列、外部适配。
- `backend/app/workers`：异步任务入口；耗时任务不得占用 HTTP 线程。
- `apps/web/src`：页面、组件、API client、类型；前端不做权威权限校验。
- `packages/contracts`：OpenAPI / JSON Schema；契约变更必须走 ADR/版本。
- `data-platform/`：解析、Canonical、处理、质检、标注、数据集等纯数据能力（按需创建）。

禁止：

- Domain 依赖 Infrastructure；跨模块导入内部实现。
- 巨型文件/函数持续膨胀（文件 500 行触发评审，800 行原则上拆分；函数 50 行有效逻辑）。
- 万能 `utils/common/helpers/base_service`。
- 复制粘贴业务规则；硬编码型号/状态/阈值/路径/密钥。
- 吞异常、空 `except`、未知状态默认成功。
- 在普通 HTTP 请求线程里处理大文件。
- 无边界、无幂等、无超时、无失败记录的异步任务。
- 未批准的微服务、CQRS、Event Sourcing、过度抽象。
- 用 Mock 数据冒充真实业务验收；Mock 必须显式标注。

## 数据与安全

- 所有项目数据由服务端强制 `project_id` 隔离；不信任客户端传入的 project_id。
- 大文件存对象存储；数据库只存元数据、引用、Hash、状态、血缘。
- 上传必须校验类型白名单、大小、内容签名、Hash；不只依赖扩展名。
- `.env`、Token、密码、客户数据不得入库/入仓/入镜像。
- 删除类操作：默认拒绝空过滤；先 dry-run 打印每条 ID + 名称；用户逐条确认；`--yes` 必须配 `--confirm-count`；删前备份；只删记录不删文件；模糊匹配禁止用于删除。
- 发布、迁移、部署、强推、重写历史必须单独批准。

## 开发与验证

- 提交前：`make check`（ruff、pytest、tsc、build）。
- 涉及数据库：跑 Alembic 升级/回滚检查。
- 涉及权限：必须有越权与跨项目隔离测试。
- 涉及文件处理：必须有多格式/损坏文件/时间同步测试。
- 涉及前端：必须覆盖 Loading/Empty/Error/Forbidden/Partial/Stale/Disabled。
- 未跑测试不得声称通过；不能为了让测试变绿而放宽断言或删除用例。

## Git 与平台

- Conventional Commits：`feat|fix|refactor|docs|test|perf|build|ci|security`。
- 使用任务分支 + worktree；共享契约/迁移/权限/路由只能单一 Owner 修改。
- 不用 `git add -A`；不覆盖其他会话改动；不 `git stash`。
- `main` 走 PR；不因本地测试通过就自动合并或发布。
- 所有交付附：变更摘要、测试命令/结果、未验证范围、风险、回滚点。

## 当前阶段

阶段 0（基建）。未经批准，不开始业务模块开发，不执行部署，不修改生产数据。
