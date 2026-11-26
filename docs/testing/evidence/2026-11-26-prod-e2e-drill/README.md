# 生产环境端到端演练证据（2026-11-26）

> 用户明确要求"不设 staging，全部在 prod 验证"。本证据记录在生产环境
> （docker-compose 数据面 + 真实 PG/MinIO + 真实迁移后库）上跑通的完整链路，
> 以及演练中**发现的真实缺陷**。

## 环境

| 项 | 值 |
|---|---|
| main 基线 | `8b0abdc`（#141 合并后） |
| 生产 PG | `ddp-postgres`，PostgreSQL 16.16 (alpine)，库 `ddp` @ `localhost:55432` |
| 迁移前版本 | `0074` |
| 迁移后版本 | `0082`（本次升级，5 张新表落地） |
| API 实例 | 临时 `uvicorn` @ `127.0.0.1:8077`，环境变量指向**生产库**（非测试库） |
| 演练项目 | `1bca073d-c9af-4601-9b0f-7888d0747db0`（code `f-01f8b8`） |

## 迁移执行纪律（阶段4「可逆等价」）

1. **备份点**：`pg_dump -Fc` → `/tmp/ddp-pgbackup/ddp-pre0082-0837.dump`（5.7 MB）
2. **一次性验证库**：`pg_restore` 到 `ddp_migration_check`（非 staging，是丢弃式核对库），
   跑 `0074 → 0082`（5 个迁移）→ `0082 → 0074`（5 个 downgrade）→ 再 `0074 → 0082`，
   全部成功；验证后 `DROP DATABASE`
3. **生产实跑**：仅在验证通过后对生产库执行 `alembic upgrade head`
4. **落地确认**：`prm_annotations` / `ai_token_usage` / `annotator_skill_tags` /
   `annotation_honeypot_state` / `annotation_backflow_cases` 五表齐备

## 演练结果

### ✅ 1. BadCase 回流受理（蓝图 N5/G5）
```
POST /projects/{id}/annotation-backflow
→ 200 {"accepted":1,"duplicated":0,"rate_limited":0,
       "decisions":[{"decision":"CREATE_TASK","suggested_priority":"P2",...}]}
```

### ❌→✅ 2. 幂等重发（**演练发现的真实缺陷**）
- 首次演练：重发同一 `(source, case_ref)` → **409 duplicate_resource**（与契约承诺相反）
- 根因：跳过判决也写行，撞唯一键
- 修复：PR #143（跳过/限流判决不落库）
- 复测：`200` + `DUPLICATE_SKIPPED`，表内仍仅 1 条受理行

### ✅ 3. 回流列表可见
```
GET /projects/{id}/annotation-backflow
→ 200 {"items":[{...,"status":"RECEIVED","suggested_priority":"P2",...}]}
```

### ✅ 4. PRM 步级标注（蓝图 N1）
```
POST /projects/{id}/prm-trajectory-annotations
→ 201 {"trajectory_id":"prod-drill-traj-1","step_count":3,"marked_count":2,
        "total_reward":-1.0,"label_counts":{"CORRECT":1,"FATAL":1}}
```
reward 映射正确：CORRECT=+1.0、FATAL=-2.0。

### ✅ 5. Fork 分叉重写 → Step-DPO 对（蓝图 N1）
```
POST .../prm-trajectory-annotations/prod-drill-traj-1/fork
→ 200 {"fork_point_index":1,"forked_steps":["读取文件","正确修改","运行测试"],
        "shared_prefix_steps":1,
        "dpo_pair":{"prompt":"任务：修复测试失败\n已执行步骤：\n[0] 读取文件",
                    "chosen":"[1] 正确修改\n[2] 运行测试",
                    "rejected":"[1] 错误修改\n[2] 运行测试"},
        "preference_json_value":{...}}
```

### ✅ 6. Token 成本台账（蓝图 N6）
```
GET /projects/{id}/ops/token-ledger
→ 200 {"entries":[],"total_cost_estimate":null,"generated_at":"..."}
```
空态语义正确：`null`（未配价/无数据）而非 `0`（免费）。

### ✅ 7. 黄金题熔断 resume（蓝图 N3/G2）
```
POST /projects/{id}/annotation-honeypot/{annotator_id}/resume
→ 200 {"state":"NORMAL","resumed":false,...}
```
无状态行时幂等返回 NORMAL，不误报错误。

### ✅ 8. 训练导出链路（蓝图 N4）
SFT 导出产出 `sft_messages.jsonl` + `provenance.jsonl`，manifest 带 sha256：
```
{"format":"SFT_MESSAGES_JSONL","record_count":1,
 "files":{"sft_messages.jsonl":"sha256:b23ac624...","provenance.jsonl":"sha256:ab405a36..."}}
```

## 结论

**生产链路的 8 个环节全部实跑通过（1 处缺陷已修复）**：
回流 → 幂等 → 列表 → PRM 步级标注 → fork/Step-DPO → Token 台账 → 熔断治理 → 导出。

## 诚实边界

- 演练是**单机小样本**（每环节 1–3 条数据），不等于容量/并发验证（压测见 P3-2 交付）
- 未验证：训练侧真实消费（本仓不含 trainer）、跨项目并发写、PG 故障切换
- API 实例为临时进程（非常驻），生产常驻部署与进程管理不在本次范围
- 演练只覆盖阶段 0–3 已合并能力；阶段 4 新增能力（语义去重/技能写入/fork 物化）待其 PR 合并后补测
