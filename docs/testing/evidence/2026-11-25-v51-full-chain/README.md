# V5.1 全链路真实测试报告（2026-11-25）

- 结论：**PASS** —— 真实机器人数据走通「对象存储 → 物化阶段 → 版本化产物」
- 基线：`main @ 65be548`，迁移已升级到 **0073**
- 证据等级：**E1（本次实测，真实样本 + 真实 MinIO + 真实 PostgreSQL）**

---

## 1. 部署状态

| 项 | 值 |
|---|---|
| 代码 head | `65be548` |
| 数据库迁移 | **0069 → 0073**（4 个迁移成功应用） |
| 真实 PG 表数 | 96 |
| MinIO | healthy（43h uptime） |
| 应用装配 | OK（processing 路由 6 条） |

### 迁移应用明细
```
0069 → 0070  V31-030 质量运行生命周期 lifecycle
0070 → 0071  V31-011 Edge Capture Agent 身份/配对/心跳/Lease 表
0071 → 0072  V4.1 运维修复 outbox 历史行归正
0072 → 0073  V51-010 质量结论证据定位列
```

### 迁移后结构验证
- `quality_findings` 新增 4 列：`evidence_start_time_ns` / `evidence_end_time_ns` / `evidence_stream_id` / `evidence_field_path` ✅
- `edge_agents` / `agent_heartbeats` / `agent_task_leases` / `edge_agent_pairing_tokens` 四表齐全 ✅

---

## 2. 全链路测试结果

### 样本
| 项 | 值 |
|---|---|
| 来源 | HuggingFace `amandlek/robomimic` rev `74fa018` |
| 文件 | `v1.5/lift/ph/demo_v15.hdf5` |
| 大小 | 36,699,586 B |
| SHA256 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |

### 链路与实测输出

```
[1] 上传真实样本到 MinIO
    key=raw/<project>/v51-e2e/<run_id>/demo.hdf5
    size=36,699,586B  ✅

[2] 构造 STREAM_EXTRACTION 阶段（真实 reader + 真实 artifact store）

[3] 执行阶段
    status = SUCCEEDED
    summary = 提取 3 个 demo（layout=robomimic_hdf5_v1.5）：
              observation 174 条，action 174 条
    artifacts = 3

[4] 校验产物
    [0] 0000.ndjson  78,343B  kind=STREAM_EXTRACTION  streams=2  action_source=PRESENT
    [1] 0001.ndjson  75,238B  kind=STREAM_EXTRACTION  streams=2  action_source=PRESENT
    [2] 0002.ndjson  73,737B  kind=STREAM_EXTRACTION  streams=2  action_source=PRESENT

[5] 清理 4 个对象
```

### 6 项检查全部 PASS

| 检查 | 结果 |
|---|---|
| 真实样本上传 MinIO | ✅ |
| 阶段执行成功 | ✅ `SUCCEEDED` |
| 产出 Artifact | ✅ 3 个 |
| 产物类型正确 | ✅ 全部 `STREAM_EXTRACTION` |
| 产物有 checksum | ✅ |
| 产物可读回 | ✅ |

---

## 3. 链路打通的意义

**在此之前**（S0 审计实测）：
- `processing_artifacts` 表 **0 行**
- 所有物化阶段 **SKIPPED**
- `SensorStream`/`Observation`/`Action` 类型定义存在但**全仓零处实例化**

**现在**：真实样本 → 真实对象存储 → 真实阶段执行 → **真实产物落盘**，174 条 observation + 174 条 action 可从 MinIO 读回。

这是 V5.1「从数据管理工具走向训练数据生产系统」的第一步实证。

---

## 4. 诚实边界

| 项 | 状态 |
|---|---|
| 真实样本 → 物化产物 | ✅ 已验证 |
| 产物持久化与读回 | ✅ 已验证 |
| **写入 `processing_artifacts` 表** | ⚠️ 本测试**未**走完整 Pipeline 编排（直接调 handler），表记录需 Pipeline 执行才写入 |
| 真实硬件采集 | ❌ `HARDWARE_CAPTURE_NOT_TESTED` |
| 图像观测 | ❌ 样本无图像 |
| 多 Episode 数据集 | 部分（3 个 demo 已验证） |

---

## 5. 复现

```bash
# 前置：样本已下载并有 SHA256 校验
cd backend
PYTHONPATH=".:../data-platform" .venv/bin/python \
  ../docs/testing/evidence/2026-11-25-v51-full-chain/full_chain_test.py
```
