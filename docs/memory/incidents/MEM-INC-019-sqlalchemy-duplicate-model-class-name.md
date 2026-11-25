### MEM-INC-019 | SQLAlchemy 模型类名全局唯一：pytest filterwarnings=["error"] 把重复类名告警升级为硬错误

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `数据库模型/测试门禁` | 批次/资产 `backend/app/infrastructure/db/models/` | 密级 `INTERNAL`
- **核心结论**：SQLAlchemy 的 declarative 基类要求模型类名（`__tablename__` 之上的 Python 类名）在整个 registry 内全局唯一；重复类名只产生 `SAWarning`，但本仓 `backend/pyproject.toml` 的 pytest `filterwarnings = ["error", ...]` 把它升级为**硬错误**，任何相关测试在收集/导入期直接红。新增模型时类名必须先全局查重。
- **关键依据与细节**：
  - 实测（2026-11-25，PR #135 Token 成本台账）：`AiTokenUsage` 命名踩到与既有类重名，SAWarning 被 `filterwarnings=["error"]` 拦下，测试直接失败；重命名模型类后恢复。
  - 门禁位置：`backend/pyproject.toml` `[tool.pytest.ini_options]` 的 `filterwarnings = ["error", "ignore:.*anyio.abc.BlockingPortal.*:DeprecationWarning"]`（第 23 行起）。
  - 隐蔽点：本地不跑 pytest 只跑 alembic 命令时看不到告警；CI 的 pytest 阶段才会炸，容易误判为迁移问题。
- **影响与后续动作**：
  - 新建 ORM 模型前先 `grep -rn "class <Name>" backend/app/infrastructure/db/models/` 查重；重名类宁可加领域前缀也不要復用泛名。
  - 责任人：Backend Engineer；每次新增数据表/模型时执行。

---

#### 工程扩展字段

- **check_ref**：`backend/pyproject.toml`
- **recurrence**：`1`
- **tags**：alembic,model-naming,pytest,filterwarnings,sqlalchemy,sawarning
