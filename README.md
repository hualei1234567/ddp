# DDP（Embodied AI Data Platform）

面向具身智能行业的数据生产、治理、资产化与训练支撑平台。

> 当前阶段：**阶段 0（基建）**。目标是把仓库、CI、契约、本地环境、Agent 团队与平台纳管做对，
> 再按「机器纳管 → 数据采集 → 数据上传 → 数据标注 → 数据训练」主链路推进。
>
> 建设基线：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`。
> 桌面 memory 仅作参考，凡与 `docs/` 冲突，以 `docs/` 为准。

## 目录

```text
.
├── apps/web/                 # React + TypeScript + Ant Design + Vite
├── backend/                  # FastAPI + SQLAlchemy + Alembic
│   ├── app/{api,application,domain,infrastructure,workers,shared}
│   ├── migrations/
│   └── tests/
├── packages/contracts/       # OpenAPI / JSON Schema 契约
├── infra/docker/             # 镜像与 Compose 辅助
├── docs/                     # 规范、审计、计划、ADR、报告
├── scripts/                  # 开发/检查/Agent 守卫脚本
└── .github/                  # CI、CODEOWNERS、PR/Issue 模板
```

## 快速开始

```bash
# 1. 后端依赖（Python 3.11+）
make venv

# 2. 本地基础设施（PostgreSQL / Redis / MinIO）
make infra-up

# 3. 后端
make api            # http://localhost:8000/health

# 4. 前端（另一个终端）
cd apps/web && npm install && npm run dev   # http://localhost:5173

# 5. 检查
make check
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `make venv` | 创建后端虚拟环境并安装依赖 |
| `make infra-up` / `make infra-down` | 启停本地 PostgreSQL/Redis/MinIO |
| `make api` | 启动后端开发服务 |
| `make migrate` | 执行 Alembic 迁移 |
| `make test` | 后端 pytest + 前端类型检查 |
| `make lint` | ruff + eslint |
| `make check` | 完整本地检查（lint + test + 前端构建） |

## 文档

- [文档索引](docs/README.md)
- [现状审计报告](docs/01-现状审计报告-2026-11-08.md)
- [差距分析与实施计划](docs/02-差距分析与实施计划-2026-11-08.md)
- [1+6 团队与平台纳管方案](docs/03-1+6团队与平台纳管方案-2026-11-08.md)
- [ADR](docs/adr/)

## 工程红线

- 不以「快速交付」为理由累积结构性技术债务。
- 不硬编码机器人型号、传感器类型、格式映射、业务状态、阈值、路径和密钥。
- 不吞异常；不无限重试；不做无幂等的异步任务。
- 大文件只进对象存储，数据库只存元数据与引用。
- 所有项目数据由服务端校验 Project Scope，不信任客户端 `project_id`。
- 不可逆操作先 dry-run、逐条确认、带计数、先备份。
- 未验证必须标注 `NOT RUN` / `BLOCKED`，不得把规划当实现。

## 阶段路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 仓库、CI、契约、环境、平台纳管、团队 | 进行中 |
| 1 | 项目基础 + 机器/设备/传感器纳管 | 待开始 |
| 2 | 采集 + 上传接入 + Episode | 待开始 |
| 3 | 标注 + 最小质检 | 待开始 |
| 4 | 数据集 + 训练支撑 | 待开始 |
| 5 | 质检/资产/模型闭环 | 待开始 |
| 6 | 规模化运营 | 待开始 |

## 许可证

内部项目，未开放外部使用。
