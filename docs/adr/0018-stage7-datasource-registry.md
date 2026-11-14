# ADR-0018 阶段 7 数据源登记域决策

- 状态：**ACCEPTED（已实现并冻结 `ddp.stage7/1.0.0` 第一批）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0002、ADR-0003、ADR-0004、ADR-0014、ADR-0017；
  `docs/ddp/07-数据治理与HuggingFace-MVP规范.md`；`docs/ddp/03-模块映射与优先级.md` §3；
  审计报告 §7（数据平面 MISSING）
- 边界：只定义"外部数据源登记"这一层的边界与约束；解析、审看、审核不在本 ADR 内。

## 1. 背景

审计结论：`data-platform/` 目录不存在，`docs/ddp/07` 描述的整条数据链路
（登记 → 导入 → 校验 → 解析 → 审看 → 审核 → 质量报告 → Dataset Version）为 MISSING。
这是当前 MVP 最优先缺口。

`docs/ddp/03` §3 给出的顺序是：先登记，再导入，再解析。本 ADR 落地第一步。

## 2. 分层决策

| 层 | 位置 | 职责 | 依赖 |
|---|---|---|---|
| 纯数据能力 | **`data-platform/`（新建）** | HF 元信息 Adapter、将来的解析器 | 只依赖 `huggingface_hub` 等数据 SDK |
| 领域 | `backend/app/domain/datasources/` | `DataSource` / `DataSourceImport` 聚合、导入状态机 | 无（纯 Python） |
| 应用 | `backend/app/application/datasources/` | 用例编排、幂等、审计 | 领域 + 端口协议 |
| 基础设施 | `backend/app/infrastructure/` | ORM（迁移 0024）、SQL 仓储 | SQLAlchemy |
| 接口 | `backend/app/api/v1/datasource_*` | HTTP 协议适配 | FastAPI |

**为什么把 Adapter 放 `data-platform/` 而不是 backend**：`CLAUDE.md`「代码边界」明列
`data-platform/` 放"解析、Canonical、处理、质检等纯数据能力"。HF 元信息读取属于
外部数据源接入，且将来 FFmpeg / h5py 等重依赖不应污染 backend 的 requirements。
依赖单独列在 `data-platform/requirements.txt`。

`data-platform/` 通过 `ddp_data.huggingface.HuggingFaceAdapter` 暴露能力，backend 由
`DataSourceUseCases` 在**未来的导入 worker**中调用。本批只登记任务，worker 在下一批。

## 3. 关键决策

### 3.1 `revision` 必须解析为 commit sha

HF 的 `main` 会移动。如果只存 `main`，"同一 revision"在不同时间指向不同数据，
导入不可复现，Dataset Version 的血缘也就不可信。

**决策**：Adapter 从 `dataset_info().sha` 取 commit sha；解析失败（空串或分支名）
直接抛 `HfAdapterError`，**拒绝登记**。该约束同时写在：

- OpenAPI `pattern: ^[0-9a-f]{40}$`
- Pydantic `Field(min_length=40, max_length=40, pattern=...)`
- 数据库 `CHECK (length(revision) = 40)`

三层都拦，任一层被绕过仍不成立。迁移测试直接验证数据库层拒绝 `revision='main'`。

### 3.2 许可证与访问条件

`docs/ddp/07` §2 明确："不得把访问成功等同于拥有商业使用权。"

**决策**：
- `license` 必填（空字符串在领域层就拒绝）；
- Adapter 在 Dataset Card 无 `license` 时返回 `None`，**不猜**；
- `access_condition` 与 `is_gated` 必须一致（GATED ⟺ is_gated），三层同时校验；
- `updateDataSource` 允许改 `license` / `access_condition`：数据集页面的许可声明可能更新，
  但**改 revision 必须重新登记**——改 revision 等于换数据，血缘必须断开重连。

### 3.3 导入范围默认小样本

**决策**：`scope=SAMPLE` 时必须给 `sample_limit`（领域的 `DataSourceImport.__post_init__`
与数据库 CHECK 都拦）。默认不提供 `FULL` 的便利路径——全量下载是显式选择。

### 3.4 复用而非重建

本域不新建审计、outbox、幂等表或实现：

| 能力 | 复用自 | 说明 |
|---|---|---|
| 幂等 | `application/idempotency.py` + `infrastructure/repositories/idempotency.py` | 批次 1 收敛出的唯一实现 |
| 审计 + outbox | `application/audit.py` | 批次 2 收敛出的唯一实现 |
| 项目隔离 | `ProjectScope` + `scoped_select` | 全局约定 |
| 游标分页 | `dataset_pagination` | 与阶段 2–6 一致 |
| 错误映射 | `api/errors.py` 新增 `datasource_error_to_app` | 与其他域同构 |

`application/datasources/idempotency.py` 是薄适配层（51 行），与其他五个域一致。

## 4. 数据模型

`data_sources`：
- 唯一键 `(project_id, code)` —— 项目内编码唯一；
- 唯一键 `(project_id, repo_id, revision, config_name, split)` —— 同一份数据只登记一次，
  否则血缘会分叉（仓储层显式用 `IS NULL` 匹配，因为唯一约束里 NULL 互不相等）。

`data_source_imports`：
- 失败也留记录（`error_message` 必填），符合 `docs/ddp/07` §5"处理失败保留错误和任务状态"；
- 与 `data_sources` 外键 `ON DELETE CASCADE`。

两者都继承 `ProjectScopedMixin`（project_id / version / created_* / updated_*）。

## 5. 事件

登记导入任务时投递 `datasource.import_requested`，payload 含
`import_id` / `data_source_id` / `repo_id` / `revision` / `scope` / `sample_limit`。

**为什么现在投递而不是等 worker 就绪**：审计 R-13 指出"78 种 outbox 事件仅 2 种有
消费者"。本域不重复这个问题——事件 payload 已完整到可离线重放，且本 ADR 明确登记
"消费者在下一批实现"，不是"投递了但没人知道要消费"。

## 6. 未做的事（下一批）

1. 导入 worker：消费 `datasource.import_requested`，用 `huggingface_hub` 流式下载小样本到
   缓存目录，更新 `data_source_imports` 状态与行数/文件数；
2. 文件校验：复用既有上传链路的类型白名单/大小/内容签名/hash 校验；
3. 解析：MP4 / MCAP / HDF5 → Canonical；
4. 视频审看前端（ADR-0017 已定选型，等解析层产出真实媒体后再接入）。

## 7. 回滚

- 契约：基线 `openapi.stage7.v1.0.0` 受 CI SHA 强制；回滚需改回 v1.4.0 并同步基线；
- 数据库：迁移 0024 的 `downgrade` 完整删表，已在真实 PG 上验证；
- 代码：`data-platform/` 为新增目录，删除不影响既有模块。
