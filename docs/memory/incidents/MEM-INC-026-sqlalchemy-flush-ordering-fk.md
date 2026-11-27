### MEM-INC-026 | SQLAlchemy 批量插入时父行必须先显式 flush——依赖排序不可靠，子行 FK 检查会失败

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `测试种子/数据装配` | 批次/资产 `backend/tests/application/` | 密级 `INTERNAL`
- **核心结论**：在同一个 Session 里批量 `add()` 父行与子行后直接 `commit()`，SQLAlchemy **不保证**按外键依赖顺序 flush。子行的复合外键检查可能在父行落库之前执行，报 `FOREIGN KEY constraint failed`。**修法是在添加父行后显式 `session.flush()`**，而不是依赖框架的排序。
- **关键依据与细节**：
  - 实测（2026-11-27，数据集版本对比测试）：同一 Session 内 add Project/User → add Dataset → add DatasetVersion → add DatasetItem → commit，报 `FOREIGN KEY constraint failed`。
  - 根因：`dataset_items` 的复合外键 `(project_id, dataset_version_id)` / `(project_id, episode_id)` 要求父行先落库；SQLAlchemy 的 flush 顺序按对象依赖图，但**未声明的隐式依赖**（如"条目引用版本"）不在图里，顺序不保证。
  - 修复：每层父行 `add()` 后立即 `flush()`（Project/User → flush → Dataset → flush → Version → flush → Item）。
  - 同族问题：唯一约束 `(project, version, episode)` 要求**每条目独立 episode**，同版本同 episode 只允许一条。
- **影响与后续动作**：
  - 所有"多实体级联种子"的测试都应按层 flush，不要依赖单次 commit。
  - 若 FK 报错，先检查父行是否已 flush，而不是先怀疑模型定义。

#### 工程扩展字段

- **check_ref**：`backend/app/infrastructure/db/models/datasets.py`（复合外键定义所在；测试侧种子见 PR #166）
- **recurrence**：`1`
- **tags**：sqlalchemy,flush-ordering,foreign-key,test-seeding,composite-fk
