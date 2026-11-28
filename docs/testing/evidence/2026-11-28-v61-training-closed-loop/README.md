# 2026-11-28 V6.1 Sprint 5 · T3-04 训练证据→评测→Badcase→补采需求→复测关闭全闭环 E1 报告

- **任务编号**：`V6.1 Sprint 5 · T3-04`
- **执行脚本**：[`scripts/e2e/v61_training_closed_loop.py`](../../../../scripts/e2e/v61_training_closed_loop.py)
- **配套测试**：[`scripts/tests/test_v61_training_closed_loop.py`](../../../../scripts/tests/test_v61_training_closed_loop.py)
- **原始结果 JSON**：[`closed-loop-result.json`](./closed-loop-result.json)
- **基线提交**：`711e5c989d36956761a904d6f5560765434c1eaf`
- **执行环境**：Python 3.11.17 / `macOS-27.2.1-arm64-arm-64bit`
- **总耗时**：`0.988s`（全部 10 段 HTTP API 业务闭环 **PASSED**）

> **诚实边界声明**：
> - 默认模式在进程内运行真实 FastAPI 应用（`TestClient`）与内存 SQLite，完整验证全部真实 HTTP 路由、RBAC 职责分离（Editor 提交 / Owner 审批与关闭）、乐观锁 `If-Match`、状态机与数据库落库；
> - 训练证据载荷来自仓库已归档的真实 GATE-B 证据（[`docs/testing/evidence/2026-11-28-gate-b-training/evidence.json`](../2026-11-28-gate-b-training/evidence.json)），经 [`scripts/train/gate_b_video_train.py`](../../../../scripts/train/gate_b_video_train.py) 的 `build_evidence_payload` 转换并落入 `training_run_evidence` 表；
> - 对象存储、模型产物检查器与外部训练 Adapter 在进程内使用显式替身；针对已部署实时栈的联调请使用 `--live-url <http(s)://...>`。

---

## 1. 全闭环 10 段实测结果摘要

| 阶段 | 核心接口 / 动作 | 关键结果与证据锚点 |
|---|---|---|
| 1. 项目/数据集/版本与导出 | `POST /datasets` → `versions` → `build` / `validate` / `freeze` → `POST /exports` + `ExportWorker` | `episode_count=10`，版本状态 `FROZEN`（`575f5de4-2634-40c0-a103-8ebb387d5878`），导出 `SUCCEEDED` |
| 2. 训练需求/交付/外部引用 | `POST /training-requests` → `submit` / `approve` → `POST /training-deliveries` (`ready` → `deliver`) → `POST .../external-references` | 交付状态 `DELIVERED`，外部引用 `0c324100-9c31-4432-a6be-512d114bd3c6`，`execution_mode=LOCAL` |
| 3. 真实训练证据落库 | `POST /external-training-references/{ref_id}/evidence`（复用 `gate_b_video_train.build_evidence_payload`） | `evidence_id=915b9119-30a4-46ff-87f0-00b7c336f49f`，`run_kind=SMOKE`，`steps=2`，`exit_code=0`，`status=SUCCEEDED`，`checkpoint_sha256=sha256:46bbbabfac48233eeeb4edd72acc567eb60e49e78cfd8d6cf2df1f1056407099` |
| 4. 证据双通道回查核验 | `GET /external-training-references/{ref_id}/evidence` + `GET /training-deliveries/{delivery_id}/evidence` | 按引用命中 `1` 条、按交付命中 `1` 条，`evidence_id` 与哈希完全一致 |
| 5. 模型注册与产物校验 | `POST /models` → `POST /models/{id}/versions` → `actions/validate` | `model_version_id=ca83dcf7-d23b-4d81-878c-12dd73eee370`，状态 `READY`，`verification_status=VERIFIED` |
| 6. 首轮评测与回调判定 | `POST /evaluations` + HMAC 签名回调 `evaluation.completed` | `evaluation_id=b291beb0-ebf1-44d9-b1bf-2da8aaf96bd5`，`success_rate=0.42 < 0.90`，`verdict=FAIL` |
| 7. Badcase 登记与缺口聚合 | `POST /model-feedback` + `GET /data-gaps` | `feedback_id=bd0eb364-b132-4cdd-8e52-87e6e92b8354`，`issue_type=SCENE_COVERAGE`，`gap_open_feedback_count=1`（`MEASURED`） |
| 8. 补采需求生成与满足 | `POST /model-feedback/{id}/actions/create-requirement` → `GET /data-requirements/{id}` → `submit` → Owner `approve` → `satisfy` | `requirement_id=07afe31b-5294-48ae-99a7-224d36d34fc5`（`DR-V61-b8174f`），关联冻结版本后状态 `SATISFIED` |
| 9. 复测评测与 `link-retest` | `POST /evaluations` + 回调（`0.95`）→ `POST /data-requirements/{id}/actions/link-retest` | `retest_evaluation_id=04537a8e-0b78-4512-9d90-161cda185cfa`，`verdict=PASS`，已挂入 `retest_evaluation_ids` |
| 10. 需求关闭与反馈闭环 | `POST /data-requirements/{id}/actions/close` → `POST /model-feedback/{id}/actions/resolve` | 需求 `CLOSED`，反馈 `RESOLVED`，全闭环闭合 |

---

## 2. 复现与实时栈联调命令

### 2.1 默认进程内全闭环（无需外部数据库/MinIO）

```bash
python3 scripts/e2e/v61_training_closed_loop.py \
  --output docs/testing/evidence/2026-11-28-v61-training-closed-loop/closed-loop-result.json
```

### 2.2 实时部署栈联调（`--live-url`）

```bash
python3 scripts/e2e/v61_training_closed_loop.py \
  --live-url https://ddp.example.internal \
  --project-id "$DDP_PROJECT_ID" \
  --auth-token "$DDP_API_TOKEN" \
  --approver-token "$DDP_APPROVER_TOKEN" \
  --external-ref-id "$DDP_EXTERNAL_REF_ID" \
  --dataset-version-id "$DDP_DATASET_VERSION_ID" \
  --model-version-id "$DDP_MODEL_VERSION_ID" \
  --callback-secret "$DDP_MODEL_ADAPTER_SECRET" \
  --output /tmp/v61-live-closed-loop.json
```

---

## 3. 门禁验证记录

- `pytest -v scripts/tests/test_v61_training_closed_loop.py`：**11 passed**（含 URL scheme / live 参数校验、`build_evidence_payload` 规范测试与进程内全闭环 E2E）
- `ruff check` & `ruff format --check`：**0 errors**
- `python3 scripts/check_secrets.py`：**PASSED**
- Semgrep SAST（`/tmp/semgrep-venv/bin/semgrep`）：**0 findings**
