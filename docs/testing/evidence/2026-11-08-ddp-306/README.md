# DDP-306 阶段 2 测试策略与样例包证据

- 任务：DDP-306「阶段 2 测试策略与采集样例包」
- 角色：QA & DevOps
- 日期：2026-11-08
- 分支：`chore/ddp-qa`
- 提交：`e048cb9`
- 环境：Linux 本地 worktree；Python 3.11.18；未连接 PostgreSQL/MinIO/Redis/真实设备
- 交付：
  - `docs/testing/阶段2测试策略与验收矩阵.md`（QA-TS-002）
  - `docs/testing/阶段2采集样例包设计与清单.md`（QA-SP-002）
  - `docs/testing/fixtures/stage2/`（生成器、样例、manifest、SHA256SUMS）

---

## 1. 范围

本次只做阶段 2 测试设计、验收矩阵与合成样例包：

1. `S2-T01`–`S2-T30` 映射到测试层级、用例、样例、证据与 AC；
2. 多相机 + 机器人状态、损坏/非法/超大、断点/重复上传、时间同步、Episode 边界、跨项目、弱网；
3. 样例包生成、清单、SHA256 与可复现复核；
4. 失败与弱网验收口径。

**未做：** 阶段 2 业务测试执行、PG/MinIO 集成、E2E、真实采集、性能、恢复演练、发布。

---

## 2. 命令与真实结果

| 命令 | 退出码 | 结果 | 状态 |
|---|---|---|---|
| `backend/.venv/bin/python docs/testing/fixtures/stage2/generate_stage2_samples.py` | 0 | `生成完成：35 个文件，598603 字节` | PASS（样例包生成） |
| 同上（第二次完整生成） | 0 | `生成完成：35 个文件，598603 字节`；manifest 未变化 | PASS（可复现） |
| `backend/.venv/bin/python docs/testing/fixtures/stage2/generate_stage2_samples.py --check --report docs/testing/evidence/2026-11-08-ddp-306/fixture-check.txt` | 0 | 3 项 PASS，见 `fixture-check.txt` | PASS |
| `backend/.venv/bin/python -c "…samples.manifest.json…"` | 0 | SHA256 `715b0303a78d451a940c1b55f4d69403f2d36a0430478f5ce8dce0a7fe1092f8`；18,761 B | PASS（清单） |
| 阶段 2 pytest / Alembic / MinIO 集成 / E2E / 弱网 / 真实采集 | 未执行 | 阶段 2 代码、迁移、环境未就绪 | NOT RUN / BLOCKED |

### 2.1 样例包复核输出

```text
PASS samples.manifest.json 一致（35 个文件）
PASS SHA256SUMS 一致
PASS 逐文件 SHA256 与大小一致
```

### 2.2 过程说明

首次执行 `--check` 时，复核进程未加载已登记元数据，导致重建 manifest 与落盘 manifest 不一致（文件哈希本身未变）。已修正生成器的 `--check` 逻辑：从既有 manifest 读取元数据后重算并比对；修正后连续两次完整生成 + 复核均 PASS。该问题属于测试工具缺陷，不影响样例文件内容。

---

## 3. 样例包统计

| 项 | 值 |
|---|---|
| 文件数 | 35 |
| 总字节 | 598,603 |
| `SYNTHETIC` | 20 |
| `SYNTHETIC_NEGATIVE` | 10 |
| `SYNTHETIC_SPEC` | 4 |
| `MOCK_SPEC` | 1 |
| Manifest | `docs/testing/fixtures/stage2/samples.manifest.json` |
| Manifest SHA256 | `715b0303a78d451a940c1b55f4d69403f2d36a0430478f5ce8dce0a7fe1092f8` |
| SHA256SUMS | `docs/testing/fixtures/stage2/SHA256SUMS` |

---

## 4. S2-T 状态

`S2-T01`–`S2-T30` 全部 `NOT RUN`。原因：

1. `backend/app` 未发现 `collection`、`ingestion`、`episodes` 模块；
2. `backend/migrations/versions` 仅有 `0001`–`0005`，无阶段 2 `0006`–`0010`；
3. `ddp.stage2/1.0.0-draft` 未冻结；ADR-0010 为已提议；
4. 本环境未启动 PostgreSQL/MinIO/Redis，且不具备真实设备/真实数据。

逐条映射见表 QA-TS-002 §4.2；状态不得写成 PASS。

---

## 5. 未验证范围与风险

| 项 | 状态 | 原因/影响 |
|---|---|---|
| 阶段 2 业务自动化测试 | NOT RUN | 被测对象未实现 |
| PostgreSQL 迁移/触发器/并发 | NOT RUN/BLOCKED | 迁移未实现；本环境无 PG |
| MinIO 分片/ListParts/签名 URL | BLOCKED | 本环境无对象存储 |
| 时间同步具体阈值 | BLOCKED | 项目阈值未冻结；只能验证失败关闭与显式状态 |
| 弱网/离线 | BLOCKED | 方案未验证；只承诺分片续传与幂等 |
| 真实采集（AC-E2E-02） | BLOCKED | 无真实设备/人员/标定 |
| 性能/RPO/RTO | NOT RUN | 未测量，不得承诺 |
| 真实媒体解码/解析 | NOT RUN | 样例为签名容器，不可替代 T1/REAL |

---

## 6. 结论

- 本任务交付的测试策略、S2-T 映射、样例包生成与复核为 **PASS**；
- 阶段 2 业务测试与验收为 **NOT RUN/BLOCKED**，不得标记 Test Passed 或 QA Accepted；
- 允许进入阶段 2 契约评审、测试骨架编写与实现任务拆解；
- 不允许进入阶段 3、发布或生产部署；
- 发布、迁移、真实采集需用户单独批准。

---

**证据结束。**
