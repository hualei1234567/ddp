### MEM-INC-030 | CI 的 security job 只装 pytest/pip-audit/pyyaml——`scripts/` 下测试文件不得顶层 import 数据库驱动

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `CI/脚本测试` | 批次/资产 `scripts/tests/` `.github/workflows/ci.yml` | 密级 `INTERNAL`
- **核心结论**：CI 的 `Secret / 依赖 / Agent 守卫` job 只 `pip install pytest pip-audit pyyaml`（**不装后端依赖**），却会跑 `python -m pytest scripts/tests -q`。因此 `scripts/` 下的测试文件**不能**通过被测模块间接引入 `sqlalchemy` / `psycopg` 等数据库驱动——顶层 import 会让该 job 直接 `ModuleNotFoundError` 收集失败。
- **关键依据与细节**：
  - 实测（2026-11-27，PR #171 CI）：`scripts/tests/test_table_growth_audit.py` → `ModuleNotFoundError: No module named 'sqlalchemy'`，整个 job exit=2。
  - 本地不复现的原因：开发机的 `backend/.venv` 装了全套依赖，`pytest scripts/tests` 自然能过——**本地 GREEN ≠ CI GREEN**（与 MEM-INC-013 同族）。
  - 修法：被测脚本里数据库驱动改为**函数内惰性导入**（纯函数层——阈值判定、SQL 断言、DSN 脱敏——本就不需要驱动）。`scripts/db/table_growth_audit.py` 已按此改造。
  - 验证方式（可复用）：建一个只装 `pytest` + `pyyaml` 的干净 venv，跑 `PYTHONPATH="backend:data-platform" <venv>/bin/python -m pytest scripts/tests -q`，即等价于 CI 的 security job 环境。
- **影响与后续动作**：
  - 新增 `scripts/` 下的测试或脚本时：顶层 import 只允许标准库 + `yaml`；`sqlalchemy` / `psycopg` / `httpx` 等一律函数内惰性导入。
  - 改完 `scripts/` 后用上面的「干净 venv」方式自测一次，别只在本机 venv 里跑。

#### 工程扩展字段

- **check_ref**：`scripts/db/table_growth_audit.py`（惰性导入所在处；CI 约束见 `.github/workflows/ci.yml` 的 `Secret / 依赖 / Agent 守卫` job）
- **recurrence**：`1`
- **tags**：ci-environment,minimal-deps,lazy-import,local-green-vs-ci-green,scripts-tests
