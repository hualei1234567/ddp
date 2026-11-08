# DDP-205 验收证据包

- 任务：DDP-205「设备域安全验收：负向测试与阶段 1 安全门禁」
- 基线提交：`15af240d0c59cce303cf3c9a2cb93e1fc0d72d7e`
- 分支/工作树：`chore/ddp-qa`（`/home/user/agent-team/ddp-team/worktrees/ddp-qa`）
- 执行日期：2026-11-08
- 数据等级：T2 合成数据；不涉及真实客户数据、生产凭据或真实设备。
- 说明：本目录只放 QA 探针与证据，不修改业务代码、配置、数据库、契约。

## 1. 探针清单

| 脚本 | 用途 | 运行命令 |
|---|---|---|
| `migration_probe.py` | 0003 迁移的部分唯一索引、复合外键、带数据降级 | `backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-205/migration_probe.py` |
| `negative_probe.py` | 真实 HTTP 负向：伪造主体、跨项目 target、viewer 写、超长、NaN、PATCH status | `backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-205/negative_probe.py` |
| `contract_probe.py` | 真实 FastAPI 序列化与前端 DDP-206 fixture 对比 | `backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-205/contract_probe.py` |
| `target_and_immutability_probe.py` | 数据库是否兜底 target_id 完整性与已校验标定不可变 | `backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-205/target_and_immutability_probe.py` |

## 2. 实测输出摘要

### 2.1 migration_probe.py

```text
[PASS] 部分唯一索引定义
[PASS] devices 复合外键元数据
[PASS] 正向复合外键
[PASS] 跨项目复合外键
[PASS] ACTIVE 记录写入
[PASS] 重复 ACTIVE
[PASS] 非 ACTIVE 同目标
[PASS] 带数据降级 0003 -> 0002
[WARN] 降级数据保留语义（0003 降级直接删除设备域表，带数据降级会丢失设备域数据）
```

### 2.2 negative_probe.py

```text
[PASS] 伪造 performed_by: status=422 validation_failed
[PASS] 伪造 validated_by: status=422 validation_failed
[PASS] 跨项目 target_id: status=422 calibration_target_invalid
[PASS] viewer 写设备: status=403 forbidden
[PASS] 超长 code: status=422 validation_failed
[PASS] NaN sample_rate_hz: status=422 validation_failed
[PASS] PATCH status: status=422 validation_failed
[NOT RUN] 设备 DELETE 端点: 未实现，方法返回 405
```

### 2.3 contract_probe.py

```text
[PASS] device 字段集合: fixture=21, backend=21
[PASS] sensor 字段集合: fixture=26, backend=26
[PASS] calibration 字段集合: fixture=25, backend=25
[PASS] channels 为整数: backend=4
[PASS] uncertainty 为对象: backend={'translation_m': 0.001, 'rotation_rad': 0.0001}
[PASS] sensor_type 使用 RGB_D: backend='RGB_D'
[FAIL] scope 值大小写: backend='DEVICE', fixture 为小写 'device'
[FAIL] calibration_type 枚举: backend='EXTRINSICS', fixture 为 'EXTRINSIC'
[FAIL] sync_mode 枚举: backend='NONE', fixture 为 'hardware'
[FAIL] profile_id 为 UUID: backend 为 UUID, fixture 为 'profile-1'
[FAIL] performed_by 为 UUID: backend 为 UUID, fixture 为 'user-1'
[FAIL] checksum 为 sha256:<64hex>: backend 为 64 位十六进制, fixture 为 'sha256:abc'
[FAIL] evidence_asset_ids 为 UUID 数组: backend 为 UUID, fixture 为 'asset-1'
```

### 2.4 target_and_immutability_probe.py

```text
[FAIL] 数据库拒绝不存在 target_id: 0003 无 target_id 外键/投影表，直接写入成功
[FAIL] 数据库拒绝跨项目 target_id: 0003 无 target_id 复合外键，跨项目引用成功
[FAIL] 数据库保护已校验标定不可变: 0003 无触发器，VALIDATED 的 checksum 与 status 可被直接 SQL 改写
```

## 3. 说明

- 上述 FAIL 是本任务要暴露的残余风险，不是测试脚本缺陷；应用层/仓储层已有对应负向用例并通过。
- 本环境 Bash 守卫禁止 `docker`、`alembic` CLI 与重定向，因此：
  - Alembic 升级/降级通过 Python Alembic API 在临时 SQLite 执行；
  - PostgreSQL 集成测试本地 skip（`DDP_TEST_POSTGRES_URL` 未配置）；
  - 原始 stdout 未落盘，以上为终端实测输出摘要。
- 历史 CI 的 PostgreSQL 外键证据见 `docs/04-阶段0平台纳管与并行开工记录-2026-11-08.md` §6.13；本次未复跑，不能作为当前提交的 PASS 证据。
