### MEM-INC-020 | SQLite batch 重建撞跨表触发器：被引用表 batch 改名瞬间触发器报 no such table，处置=drop→改表→重装

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `数据库迁移（SQLite 方言）` | 批次/资产 `backend/migrations/versions/ + annotation_tasks` | 密级 `INTERNAL`
- **核心结论**：SQLite 的 `batch_alter_table` 通过「建新表 → 拷数据 → 删旧表 → 改名」重建整张表；如果**其他表的触发器**正文引用了这张表，改名瞬间解析触发器即报 `no such table: main.<表名>`。处置固定为三步：**drop 引用它的触发器 → batch 改表 → 重新安装触发器**（全部幂等）。
- **关键依据与细节**：
  - 实测（2026-11-25，PR #136 技能路由）：`annotation_tasks` 被 catalog / result / work 三组守卫触发器引用（`backend/app/infrastructure/db/annotation_catalog_guards.py`、`annotation_result_guards.py`、`annotation_work_guards.py`），batch 重建时报 `no such table: main.annotation_tasks`；按 drop→改表→重装 处置后通过。
  - 仓内先例：迁移 **0041** `0041_collection_asset_state_guards.py` 文档字符串明确记载「表被重建（batch_alter_table）时 SQLite 会在改名那一刻解析触发器而报错（0040 已实测：error in trigger ...: no such table）」，并约定「downgrade 仍先删触发器再动表」；迁移 **0052** `0052_calibration_profile_supersedes_and_guards.py` 同款处置——batch 改表前后「先 drop 再 install，两种方言都幂等」。
  - 隐蔽点：错误信息指向的是**触发器正文里的表名**，不是正在改的表，排查容易往错误方向找。
- **影响与后续动作**：
  - 凡对含守卫触发器的表（素材四表、annotation_* 系列）做 batch 重建，迁移里必须显式 drop 相关触发器并在 upgrade 尾部重装；先查 `backend/app/infrastructure/db/*_guards.py` 里谁引用了目标表。
  - 责任人：Backend Engineer；每次涉及这些表的迁移生效。

---

#### 工程扩展字段

- **check_ref**：`backend/migrations/versions/0041_collection_asset_state_guards.py + backend/migrations/versions/0052_calibration_profile_supersedes_and_guards.py`
- **recurrence**：`2`
- **tags**：alembic,batch-alter-table,guard-triggers,sqlite,trigger-rename,0041,0052
