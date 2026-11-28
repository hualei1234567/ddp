### MEM-INC-032 | 同进程脚本测试加载 services/alert-receiver/app 后未还原 sys.modules["app"] 导致 backend/app 导入失败

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `scripts/tests 同进程多服务测试` | 批次/资产 `scripts/ops/alerting_e2e_drill.py` | 密级 `INTERNAL`
- **核心结论**：仓库内 `backend/app` 与 `services/alert-receiver/app`（及 `services/local-adapter/app`）均使用顶层包名 `app`。若脚本在运行时向 `sys.path[0]` 插入子服务目录并导入 `app.main` 后未还原 `sys.path` 与 `sys.modules["app*"]`，同一 `pytest scripts/tests` 进程内按字母序排在后面的测试导入 `backend/app` 时会立即报 `ModuleNotFoundError: No module named 'app.api'`。
- **关键依据与细节**：
  - 实测：`scripts/tests/test_observability_drills.py` 先运行并留下 `sys.modules["app"] -> services/alert-receiver/app`，导致 `test_pilot_edge_chain.py` 在 `check.sh [6/10]` 失败（单跑任一文件均绿，合跑必红）。
  - 修法：在 `_load_receiver_build_server()` 中用 `try ... finally` 完整快照并还原 `sys.path` 与 `sys.modules["app*"]`。
- **影响与后续动作**：
  - 任何跨子服务动态加载同名 `app` 包的脚本或测试，必须使用快照还原 `sys.modules["app*"]` 与 `sys.path`，或通过子进程隔离调用。

#### 工程扩展字段

- **check_ref**：`scripts/ops/alerting_e2e_drill.py`
- **recurrence**：`1`
- **tags**：`sys-modules,namespace-collision,app-package,pytest,scripts-tests,alert-receiver`
