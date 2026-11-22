# ADR-0038 Episode 可训练口径与 Canonical 版本收敛

- 状态：**ACCEPTED（决策已定；收敛实现分两步，见 §6）**
- 日期：2026-11-22
- 决策者：System Architect；批准人：用户（长期授权）
- 任务：DDP-V31-004（`docs/roadmap/v3.1/n0/task-dag.md`）
- 关联：ADR-0035（Canonical Schema v1 只读契约）、
  `docs/data-standards/Canonical草案.md`、
  `backend/app/domain/episodes/state_machines.py`、
  `backend/app/domain/datasets/selection.py`、
  `backend/app/domain/annotation/selection.py`、
  `backend/app/application/datasources/episode_materialization.py`、
  `data-platform/ddp_data/canonical/v1.py`、
  `docs/roadmap/v3.1/n0/gap-matrix.md`（§4 版本分裂、§9 跨模块风险）
- 影响范围：Dataset Builder / 标注抽样的 Episode 状态门禁口径；Canonical 版本字面量的
  单一来源。**不改数据库、不改 API 契约、不迁移历史数据、不新增依赖。**

## 1. 背景

V3.1 任务 DAG 把两个决策点合并为 V31-004，因为它阻塞 V31-015/016/017/030 整条链：

### 1.1 决策点 D1：`COMPLETED` 能否进 Dataset Builder

Episode 状态机（`backend/app/domain/episodes/state_machines.py:113-119`）允许
`ACCEPTED --complete--> COMPLETED --archive--> ARCHIVED`，且 `Episode.complete()`
（`models.py:502-507`）强制要求 `quality_status is PASS`。但"可用于训练"的口径
在仓库里有 **三份各自内联的副本**：

| 位置 | 常量 | 取值 |
|---|---|---|
| `domain/episodes/state_machines.py:44` | `EPISODE_COVERABLE_STATUSES` | `{ACCEPTED, COMPLETED}` |
| `domain/datasets/selection.py:51` | `_EPISODE_STATUSES` | `{ACCEPTED, COMPLETED}` |
| `domain/annotation/selection.py:23` | `_ALLOWED_STATUSES` | `{ACCEPTED, COMPLETED}` |

三份当前取值一致，但没有任何测试或机制保证它们**继续**一致。`gap-matrix.md` §9
把「`COMPLETED` 绕过 Accepted 语义」列为**中高**风险，指的就是"口径靠人记"。

### 1.2 决策点 D3：Canonical 版本 `1.0.0-draft` vs `1.0.0`

- ADR-0035 冻结的只读契约版本是 `ddp.canonical/1.0.0`，
  `SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})`（`v1.py:36-37`），
  **只接受精确值**，`check_schema_version` 不做兼容猜测。
- 导入物化写死字面量 `IMPORT_CANONICAL_SCHEMA_VERSION = "ddp.canonical/1.0.0-draft"`
  （`episode_materialization.py:76`），与 ADR-0035 的冻结版本**不是同一个字符串**。
- 约 25 处测试夹具与迁移测试种子同样写 `-draft`。
- Episode 领域层（`models.py:222-231`）只校验 `min_length=1 / max_length=64`，
  不校验版本白名单；`episodes.canonical_schema_version` 列是 `String(64)`，
  无 CHECK 约束（迁移 0009）。
- 目前**没有任何代码**把 `Episode.canonical_schema_version` 交给
  `ddp_data.canonical.v1` 的校验器，所以版本分裂尚未产生运行时错误——
  但 V31-017 一旦接上共享 JSON Schema，草案版本会立刻被拒绝。

## 2. 决策

### 2.1 D1：`COMPLETED` **允许**进入 Dataset Builder 与标注抽样

**结论：保留 `{ACCEPTED, COMPLETED}` 口径，不收紧为只认 `ACCEPTED`。**

理由（按复用优先顺序逐条排除）：

1. **`COMPLETED` 蕴含 `ACCEPTED` 的质量结论**：状态机只有
   `ACCEPTED --complete--> COMPLETED` 一条入边，`Episode.complete()` 显式校验
   `quality_status is PASS`。排除 `COMPLETED` 等于让"已经完成交付"的 Episode
   反而不能进数据集，与业务语义相反。
2. **收紧会破坏既有数据**：历史 Episode 一旦 `complete` 就锁死
   （`EPISODE_DATA_FROZEN_STATUSES` 含 `COMPLETED`，触发器
   `trg_episodes_frozen_fields` 禁止改数据字段），把它们排除出数据集等于
   让已交付批次无法复现。
3. **`ARCHIVED` 仍被排除**：归档是"退出活跃集"的语义，不是"质量更好"。
   当前三处副本都不含 `ARCHIVED`，本 ADR 维持这一边界。

**风险的真实位置不是"要不要放 `COMPLETED`"，而是"`COMPLETED` 的质量前提只由
领域方法保证、没有数据库兜底"**：`trg_episodes_transition` 只校验状态对白名单，
不校验 `NEW.quality_status = 'PASS'`。绕过领域方法直接 UPDATE 的路径（脚本、
手工 SQL、未来 Worker）可以造出"`COMPLETED` 但 `quality_status != PASS`"的行。

**处置（本 ADR 只登记，实现归 V31-030 质检状态机迁移）**：给
`trg_episodes_transition` 增加 `NEW.status = 'COMPLETED' AND NEW.quality_status <> 'PASS'`
的拒绝分支，并用迁移测试证明。在此之前，Dataset Builder 的
`_check_candidate` 已经独立校验 `quality_status`（`selection.py:327-345`），
所以**当前不会把未通过质检的 `COMPLETED` 放进数据集**——这是双保险里的第一层。

### 2.2 D1 的收敛动作：三份副本加防漂移测试，不新增领域间依赖

`state_machines.py:39-43` 的既有注释已经裁决过这个问题：

> 这两个域与 episodes 域没有依赖关系，为收敛而新增领域间依赖不划算，因此本次只在
> episodes 域内提供权威常量，供新代码引用，不改既有三处。

本 ADR **维持该架构判断**，改为用测试锁住一致性：

- 新增 `backend/tests/unit/test_episode_status_alignment.py`，断言
  `EPISODE_COVERABLE_STATUSES`、`datasets.selection._EPISODE_STATUSES`、
  `annotation.selection._ALLOWED_STATUSES` 三者取值相等，且都不含 `ARCHIVED`。
- 任何一处被单独修改，测试立刻失败，必须在同一次变更里同步三处——这才是
  "单一权威"在不引入跨域依赖前提下的可行实现。

### 2.3 D3：正式版本号采用 `ddp.canonical/1.0.0`，不新开版本

**结论：`ddp.canonical/1.0.0` 就是正式版本；`-draft` 从未是受支持版本。**

理由：

1. ADR-0035 已把 v1 冻结为 `1.0.0`，字段清单、缺失语义、ID 规则均未变。
   新开 `1.1.0` 或 `2.0.0` 会暗示"契约发生了演进"，而事实是没有。
2. 草案版本 `1.0.0-draft` 只存在于 `docs/data-standards/Canonical草案.md` 的设计
   背景与导入链路的字面量里，**没有任何数据被声明为"按草案版本解析"的正式产物**。
   把它当作"已发布版本"来 major 升级，是给不存在的兼容负担付费。

### 2.4 D3 的迁移策略：**不迁移历史数据**

`episodes.canonical_schema_version` 记录的是"这一行是按哪一版 Canonical 映射出来的"
的**溯源事实**。历史导入产物写的是 `-draft`，就如实保留 `-draft`：

- 改写历史值等于伪造血缘，违反"旧版本数据不原地改写"（ADR-0035 §4）。
- 该列无 CHECK 白名单，保留旧值不会阻塞任何约束。
- 读取侧若需要统一口径，用纯函数转换器（ADR-0035 §4 已规定），而不是 UPDATE。

### 2.5 D3 的收敛动作：消除字面量副本

- `IMPORT_CANONICAL_SCHEMA_VERSION` 改为引用
  `ddp_data.canonical.v1.SCHEMA_VERSION`，不再写死字符串。
  backend 已依赖 `ddp_data`（`application/datasources/adapter_resolver.py:17`、
  `application/processing/hdf5_metadata.py:7` 等），不新增依赖。
- 新增测试断言导入物化产出的 `canonical_schema_version` ∈
  `SUPPORTED_SCHEMA_VERSIONS`，把"导入产物必须能被正式契约读取"变成可执行约束。
- **既有 `-draft` 测试夹具不批量改写**：它们构造的是"历史数据"，批量替换会产生
  数百行无意义 diff，且会掩盖"历史数据确实存在"这一事实。V31-017 在接入共享
  JSON Schema 时，按"新增夹具用正式版本、历史夹具保留"的规则处理。

### 2.6 写入入口是否校验版本白名单：**本 ADR 不引入**

`EpisodeCreate.canonical_schema_version`（`api/v1/episode_schemas.py:55`）目前是
自由字符串。是否加白名单属于 V31-017 的范围（该任务同时负责共享 JSON Schema），
本 ADR 只登记"写入入口应校验"这一结论，避免 V31-004 越界改 API 契约。

## 3. 后果

### 3.1 正面

- V31-015/016/017/030 的阻塞解除：Artifact 语义、Canonical 正式版本、质检状态机
  都有明确前提。
- 三份状态副本从"靠人记"变成"测试锁死"，`gap-matrix.md` §9 的中高风险降级。
- 导入链路不再有版本字面量副本，`SCHEMA_VERSION` 成为唯一来源。

### 3.2 代价与残余风险

| 残余风险 | 等级 | 处置 |
|---|---|---|
| `COMPLETED` 的质量前提仍无数据库兜底 | 中 | V31-030 给触发器加 `quality_status = 'PASS'` 分支；当前由 `_check_candidate` 兜住 |
| 历史 `-draft` 数据与正式版本并存 | 低 | 有意保留（溯源事实）；读取侧用纯函数转换器 |
| 测试夹具版本不统一 | 低 | V31-017 按"新增用正式、历史保留"处理 |
| 三份副本仍是三份 | 低 | 防漂移测试锁死；未来若 datasets/annotation 域确实需要依赖 episodes 域，再单独 ADR |

### 3.3 不做的

- 不把 `ARCHIVED` 纳入可训练口径。
- 不批量 UPDATE 历史 `canonical_schema_version`。
- 不在本 ADR 引入跨领域依赖、共享 JSON Schema 或 API 契约变更。

## 4. 备选方案

1. **只认 `ACCEPTED`**：否决。见 §2.1 理由 1、2——与状态机语义相反，且让已交付批次
   无法复现。
2. **新开 `ddp.canonical/1.1.0` 并把 `-draft` 声明为已发布版本**：否决。见 §2.3——
   契约字段未变，制造不存在的兼容负担。
3. **批量迁移历史 `-draft` → `1.0.0`**：否决。见 §2.4——改写溯源事实，违反 ADR-0035 §4。
4. **让 datasets/annotation 域直接 import `EPISODE_COVERABLE_STATUSES`**：否决。
   与 `state_machines.py:39-43` 的既有架构判断冲突；用测试锁一致性即可达到同样效果，
   且不引入领域间耦合。

## 5. 验证

```bash
cd backend && .venv/bin/python -m pytest -q \
  tests/unit/test_episode_status_alignment.py \
  tests/unit/test_episode_domain.py \
  tests/unit/test_dataset_domain.py \
  tests/infrastructure/test_import_episode_materialization.py
```

- `test_episode_status_alignment.py`：三处状态集合相等、都不含 `ARCHIVED`；
  导入物化产出的版本 ∈ `SUPPORTED_SCHEMA_VERSIONS`。
- 其余三个文件：既有行为不变（`COMPLETED` 仍可被选中、导入仍能物化）。

## 6. 实现拆分

| 步骤 | 内容 | 归属 |
|---|---|---|
| 1（本 ADR 一并落地） | 防漂移测试 + 版本常量单一来源 + 导入版本断言 | V31-004 ✅ |
| 2（已落地，见 ADR-0040） | 共享 JSON Schema、写入入口版本白名单、测试夹具版本策略 | V31-017 ✅ |
| 3 | `trg_episodes_transition` 增加 `COMPLETED ⇒ PASS` 兜底 | V31-030 |
