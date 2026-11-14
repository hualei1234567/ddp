# data-platform：纯数据能力

本目录放**不依赖 Web / ORM / HTTP** 的纯数据能力：外部数据集 Adapter、
文件校验、格式解析、Canonical 映射、质量检查。

## 边界（`CLAUDE.md`「代码边界」）

| 属于本目录 | 不属于本目录 |
|---|---|
| HF/LeRobot 等外部源 Adapter | 数据库读写（在 `backend/app/infrastructure`） |
| 文件格式解析（MCAP/HDF5/Parquet/视频） | HTTP 路由与鉴权（在 `backend/app/api`） |
| Canonical 字段映射与单位换算 | 事务边界与幂等（在 `backend/app/application`） |
| 质量规则与只读检查 | 状态机与领域规则（在 `backend/app/domain`） |

后端通过 `app.application.datasources.ports` 定义协议，`data-platform` 提供实现。
这样解析能力可以独立测试、独立演进，后端不被 FFmpeg / h5py 之类依赖污染。

## 依赖纪律

- 本目录的依赖单独列在 `data-platform/requirements.txt`，不混进 `backend/requirements.txt`；
- 版本一律精确固定；新增依赖必须走 ADR 或 PR 内依赖评估表；
- **许可红线**：默认只读 Dataset Card 与元信息，不默认全量下载；
  不得把"能访问"当成"有商业使用权"（`docs/ddp/07` §2）。

## 当前实现

| 模块 | 状态 | 说明 |
|---|---|---|
| `ddp_data/huggingface/` | 进行中 | HF 数据集元信息 Adapter（Repo ID / revision / license / gated） |
