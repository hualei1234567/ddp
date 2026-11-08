# ADR-0001 技术栈选型

- 状态：已提议（待用户批准）
- 日期：2026-11-08
- 决策者：System Architect / General Owner
- 关联：规范 §3、§14

## 背景

DDP 需要处理多模态大文件、异步 Pipeline、企业后台 UI，并支持后续多项目/多设备/多格式扩展。
当前仓库为空，允许在阶段 0 冻结技术栈。

## 决策

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 + FastAPI + Pydantic v2 |
| ORM/迁移 | SQLAlchemy 2 + Alembic |
| 数据库 | PostgreSQL 16 |
| 缓存/队列 | Redis 7 + Celery/Arq（阶段 2 接入） |
| 对象存储 | S3 兼容（本地 MinIO） |
| 前端 | React 18 + TypeScript strict + Ant Design 5 + Vite |
| 测试 | pytest + httpx + Playwright + ruff + tsc |
| 容器 | Docker Compose |
| CI/CD | GitHub Actions |

## 后果

- 团队与 MaterialDev 的后端栈一致（FastAPI），前端栈不同（React/AntD vs Vue/Element）。
- 允许按 `packages/ui` 逐步沉淀 DDP 自己的设计系统。
- 不在阶段 0 引入 Celery、Playwright 依赖，按阶段追加。

## 替代方案

- Vue 3 + Element Plus：与 MaterialDev 统一，但旧 DDP 经验与 AntD 表格/抽屉能力更匹配本平台。
- Django：自带 Admin 但领域分层约束弱，不符合规范的分层要求。
