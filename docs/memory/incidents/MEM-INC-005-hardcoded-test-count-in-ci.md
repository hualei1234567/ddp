### MEM-INC-005 | CI 里写死测试计数，新增用例就红

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `CI/前端 E2E` | 批次/资产 `全部新增测试` | 密级 `INTERNAL`
- **核心结论**：CI 的 E2E job 曾把 `passed===4 && skipped===1 && total===5` **硬编码**在断言里。任何新增 spec 都会让 CI 变红，而失败原因看起来像"测试坏了"，实际是"计数没更新"。**用魔法数字断言数量是反复踩的坑。**
- **关键依据与细节**：
  - 首次发生：新增 playback-control spec 后 CI 失败
  - 二次发生：mapper 计数断言 `assert 17 == 16`（新增 `edge_agent_error_to_app` 后变 17）
  - 正确做法二选一：
    1. 断言**集合相等**（如 mapper 全集）而非数量
    2. 若必须断言数量，改为从实际结果**动态推导**（如 `passed = total - skipped`）
  - 处理 E2E 计数时的正确姿势：更新计数 + 同步 skippable whitelist，并说明**为什么这次变更是合理的**（不是简单放宽）
- **影响与后续动作**：
  - 所有断言数量的地方都要复查：能否改成集合相等？
  - 新增测试导致 CI 红时，先判断是「计数过期」还是「真的坏了」，不要直接放宽
  - 责任人：全体；**已处理，但需持续警惕**

---

#### 工程扩展字段

- **check_ref**：`.github/workflows/ci.yml`（E2E job 的 passed/skipped 断言处）+ `backend/tests/unit/test_error_mapping_no_leak.py`
- **recurrence**：`2`
- **tags**：`ci,e2e,magic-number,assertion,flaky,golden`
