# DDP（Embodied AI Data Platform）

[![CI](https://github.com/hualei1234567/ddp/actions/workflows/ci.yml/badge.svg)](https://github.com/hualei1234567/ddp/actions/workflows/ci.yml)

面向具身智能数据生产、治理、资产化、训练交付与模型反馈的数据闭环平台。

DDP 采用模块化单体架构，把设备与采集、数据接入、Episode、标注、质量、Dataset、训练交付、模型评测和数据需求回流纳入同一条可审计链路。项目当前以本地开发、真实公开数据验证和工程闭环为主。

## 当前状态

- 阶段 0–6（V1.0）与 N0–N9（V2.0）已实现并完成本地验收。
- V2.1 正在按风险与优先级进行增量迭代，持续补齐真实格式接入、Processing Pipeline 和真实数据证据。
- 本地 Docker Compose 栈可运行；数据库迁移当前到 `0068`。
- staging 部署为 **BLOCKED**：目标环境尚不可达，未执行 staging 或生产发布。
- 真实机器人硬件在环验证为 **BLOCKED**：公开数据不能替代现场序列号、固件、标定和物理时钟验证。

状态统一使用以下口径：

| 状态 | 含义 |
|---|---|
| `VERIFIED` | 有代码、运行或测试证据证明可用 |
| `PARTIAL` | 已有可用能力，但仍存在明确缺口 |
| `UNVERIFIED` | 代码存在，尚未获得运行证据 |
| `MISSING` | 审计后确认缺失 |
| `BLOCKED` | 受环境、权限、硬件或外部依赖限制 |
| `PLANNED` | 已规划，尚未实现 |

当前能力、缺口和证据以以下文档为准：

- [V2.1 当前系统基线](docs/roadmap/v2.1/n0/current-system-baseline.md)
- [V2.1 能力矩阵](docs/roadmap/v2.1/n0/capability-matrix.md)
- [V2.1 Backlog](docs/roadmap/v2.1/n0/v2.1-backlog.md)
- [V2.1 风险清单](docs/roadmap/v2.1/n0/risk-list.md)
- [N0–N9 项目最终报告](docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md)

## 核心能力

| 领域 | 当前能力 | 状态边界 |
|---|---|---|
| 项目与权限 | 项目作用域、角色权限、跨项目隔离、审计与 Outbox | 服务端强制校验 `project_id`，不信任客户端作用域 |
| 设备与采集 | 机器、设备、传感器、标定、采集计划、Capture Session | 真实硬件握手与物理时钟验证仍 `BLOCKED` |
| 数据接入 | 分片上传、对象存储、Hash 校验、数据源登记、HF 导入 | URL / 本地 / S3 数据源仍为 `PARTIAL` |
| 多格式探测 | MP4、Parquet、MCAP、HDF5、rosbag1 / rosbag2 只读结构探测 | 部分格式仅有结构或 footer，不代表业务语义已解析 |
| Processing Pipeline | 文件校验、元数据提取、格式解析、时间对齐、标定校验、Episode 分段、质量预检 | `DATA_CLEANING` 权威规则仍未完成 |
| Episode 与血缘 | Episode、媒体资产、正反向血缘、规则版本追溯 | 部分 Canonical 流尚无持久化生产者 |
| 标注与质量 | Schema、任务批次、领取、提交、复核、仲裁、IAA、黄金集、质量报告 | 真实业务标签正确性仍需业务验收 |
| Dataset 与训练 | Dataset 版本、Manifest、质量门禁、训练申请、交付、外部训练引用 | 不包含通用分布式训练平台 |
| 模型与反馈 | 模型注册、评测、发布审批、推理回调、失败案例与数据需求回流 | 外部平台状态同步仍为受控接入 |

## 架构

```text
apps/web                React Web 应用
    │
    ▼
backend/app/api         HTTP、鉴权与请求适配
    │
    ▼
backend/app/application 用例编排、事务边界、DTO 转换
    │
    ▼
backend/app/domain      实体、值对象、状态机与权威业务规则
    ▲
    │
backend/app/infrastructure
                        PostgreSQL、对象存储、队列与外部平台适配

backend/app/workers     异步任务入口
data-platform/ddp_data  Canonical、格式探测与纯数据能力
packages/contracts      OpenAPI / JSON Schema 契约
```

依赖方向遵循：

```text
api → application → domain ← infrastructure
```

Domain 不依赖 Web、ORM、队列、对象存储 SDK 或云 SDK；大文件处理不占用普通 HTTP 请求线程。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic |
| 前端 | React 18、TypeScript strict、Ant Design 5、Vite |
| 数据与任务 | PostgreSQL 16、Redis、S3 / MinIO、Worker |
| 数据处理 | PyArrow、MCAP SDK、h5py、rosbags、ffprobe |
| 测试 | pytest、Vitest、Playwright |
| 工程门禁 | Ruff、ESLint、TypeScript、OpenAPI lint / diff、Secret 与 Agent 守卫 |

## 仓库结构

```text
.
├── apps/web/                 # React Web 应用
├── backend/
│   ├── app/
│   │   ├── api/              # HTTP 与鉴权适配
│   │   ├── application/      # 用例编排与事务边界
│   │   ├── domain/           # 领域模型与权威规则
│   │   ├── infrastructure/   # 数据库、存储、队列、外部适配
│   │   └── workers/          # 异步任务入口
│   ├── migrations/           # Alembic 迁移
│   └── tests/                # 后端测试
├── data-platform/ddp_data/   # Canonical、Adapter 与格式探测
├── packages/contracts/       # OpenAPI / JSON Schema
├── infra/                    # 基础设施配置
├── docs/                     # 规范、ADR、路线、审计与验收证据
├── scripts/                  # 开发检查与工程守卫
└── .github/                  # CI、CODEOWNERS、PR / Issue 模板
```

## 快速开始

### 环境要求

- Python 3.11
- Node.js 20
- Docker 与 Docker Compose
- GNU Make

### 1. 初始化配置与依赖

```bash
cp .env.example .env
make venv
cd apps/web && npm ci && cd ../..
```

### 2. 启动本地基础设施并迁移数据库

```bash
make infra-up
make migrate
```

本地基础设施默认包含 PostgreSQL、Redis 和 MinIO。

### 3. 启动后端与前端

终端一：

```bash
make api
```

后端健康检查：<http://localhost:8000/health>

终端二：

```bash
make web
```

前端开发地址：<http://localhost:5173>

### 4. 运行完整容器栈

```bash
docker compose up --build
```

Web 默认地址：<http://localhost:8088>。可通过 `DDP_WEB_PORT` 修改宿主端口。

## 开发与验证

| 命令 | 作用 |
|---|---|
| `make venv` | 创建后端虚拟环境并安装依赖 |
| `make infra-up` | 启动 PostgreSQL、Redis 与 MinIO |
| `make infra-down` | 停止本地基础设施 |
| `make migrate` | 升级数据库到 Alembic head |
| `make api` | 启动后端开发服务 |
| `make web` | 启动前端开发服务 |
| `make test` | 后端 pytest + 前端类型检查 |
| `make lint` | 后端 Ruff + 前端 ESLint |
| `make fmt` | 后端 Ruff format + 前端 Prettier |
| `make check` | 执行完整本地质量门禁 |

提交前必须运行：

```bash
DDP_ENV=local make check
```

完整门禁包括后端 lint / pytest、前端 typecheck / lint / Vitest / build、Secret 扫描、Agent Bash 守卫、Outbox 事件归类和契约一致性检查。

## 数据与安全边界

- 所有项目数据由服务端强制执行 Project Scope 隔离。
- 大文件进入对象存储；数据库只保存元数据、引用、Hash、状态和血缘。
- 上传同时校验类型白名单、大小、内容签名和 Hash，不只依赖扩展名。
- `.env`、Token、密码和客户数据不得进入仓库、镜像或普通日志。
- 外部数据固定 revision / commit，校验大小与 SHA256，并按不可信输入处理。
- 删除操作拒绝空过滤，先 dry-run、逐条确认、核对数量并备份。
- 未经明确授权，不向真实机器人发送运动、复位或其他控制命令。
- staging / 生产迁移、部署和发布必须单独批准。

## 文档入口

- [文档索引](docs/README.md)
- [统一建设总规范](docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md)
- [持续迭代治理基线](docs/ddp/00-项目基线与使用说明.md)
- [V2.1 路线入口](docs/roadmap/v2.1/README.md)
- [ADR](docs/adr/)
- [测试与验收证据](docs/testing/)
- [运维记录](docs/operations/)

## 贡献与变更

- 使用短生命周期任务分支和 Pull Request。
- Commit message 遵循 Conventional Commits。
- 契约、迁移、权限和路由等共享文件由单一 Owner 修改。
- PR 必须说明变更范围、复用点、测试结果、安全影响、风险和回滚方式。
- 不通过 `make check`、代码审查和验收证据三道门禁的变更不得合并。

详细规则见 [CLAUDE.md](CLAUDE.md) 与 [AGENTS.md](AGENTS.md)。

## 许可证

本仓库当前未声明开源许可证。除非另有书面授权，不授予复制、修改、分发或商业使用权利。
