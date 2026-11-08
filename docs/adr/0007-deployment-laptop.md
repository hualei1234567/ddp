# ADR-0007 部署形态：Mac 开发，Ubuntu 笔记本承载

- 状态：已提议（待用户批准）
- 日期：2026-11-08
- 关联：规范 §23

## 背景

用户明确：Mac 只做开发机；部署使用 Ubuntu 笔记本（xxx.xxx.xxx.xxx）。旧 4090 已不可达。
首期没有独立云资源。

## 决策

- **开发**：Mac 上编辑、跑轻量单元测试与前端 dev server。
- **运行/部署**：Ubuntu 笔记本承担 staging 与生产（首期同一台，通过端口/Compose 项目隔离）。
- **基础设施**：Docker Compose 管理 PostgreSQL、Redis、MinIO、API、Web、Worker。
- **代码同步**：GitHub 为唯一代码托管；笔记本通过只读 deploy key 拉取。
- **发布**：GitHub Actions 构建 → 笔记本拉取镜像/源码 → Compose 升级 → 健康检查 → 记录发布。
- **回滚**：保留上一版本镜像/Compose 文件与数据库备份；回滚不覆盖数据。

## 后果

- 单机承载有容量上限，需监控磁盘、内存与 MinIO 配额。
- 重构建、e2e、镜像构建全部放笔记本，不在 Mac 执行。
- staging 与 prod 必须使用不同的 Compose project 名、端口、数据库和对象存储桶。
- 笔记本不可达时发布阻塞，必须显式报告，不得假装成功。

## 替代方案

- 云服务器：当前无预算/账号，暂不采用。
- 4090：已不可达，不再作为依赖。
