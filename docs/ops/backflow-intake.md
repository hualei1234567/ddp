# 评测失败回流运营手册（annotation-backflow，V52-030）

- 版本：v1.0 · 2026-11-26
- 权威实现：`backend/app/domain/annotation/backflow.py`（判定/去重/限流纯函数）、
  `backend/app/application/annotation/backflow.py`（用例编排）、
  `backend/app/api/v1/annotation_backflow_routes.py`（HTTP 面）
- 适用角色：QA lead（受理与判定）、项目 owner（转换与升级）
- 权限：`POST` 写 = `get_write_scope`（editor 及以上）；`GET` 读 = `get_owner_scope`
  （**仅 owner 或平台管理员**，editor/QA lead 读列表会被 403）

> 本手册只写运营动作。阈值与判定逻辑以 `backend/app/domain/annotation/backflow.py`
> 为准；两处不一致时以代码为准并在同批 PR 修文档。

---

## 1. 它是什么（数据飞轮入口）

评测平台发现模型在某类 `capability` 上失败 → 回调本端 → 本端做「去重 / 限流 /
优先级映射」判定 → 受理的案例落库为 `RECEIVED` → **可选**在同一请求里转成
一个标注任务（`CONVERTED`）→ 标注产出回到训练集。

关键事实（代码已确认）：

- 一批案例**最多转成一个标注任务**（不按案例拆任务），任务 `code` 形如
  `BACKFLOW-<UTC 时间戳>-<8 位哈希>`，`name` 为 `评测回流 N 例`，优先级取
  本批案例中**最高**的 P 级（`_convert_accepted`）。
- 只有**受理成功**的案例落库；`DUPLICATE_SKIPPED` / `RATE_LIMITED` 是受理时刻
  的瞬态判定，**不落库**（避免与唯一键同键写入、也避免风暴期写放大）。
  因此 `GET` 列表里实际只会看到 `RECEIVED` / `CONVERTED` 两种状态。

---

## 2. 端点与参数（真实契约）

### 2.1 受理回调（评测平台 → DDP）

```
POST /api/v1/projects/{project_id}/annotation-backflow
```

请求体（`BackflowIngestIn`）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `cases` | array | **1..200 条** | 每条必含 `source` / `case_ref` / `capability` |
| `cases[].source` | string | 1..120 | 来源系统标识（限流按它分桶） |
| `cases[].case_ref` | string | 1..200 | 来源系统内案例唯一键（幂等键的另一半） |
| `cases[].capability` | string | 1..120 | 失败所属能力维度（参与优先级映射） |
| `cases[].failure_summary` | string? | ≤1000 | 失败摘要，平台只存不解释 |
| `cases[].failure_count` | int | ≥1，默认 1 | 累计失败次数（影响优先级） |
| `cases[].suggested_priority` | string? | `P0`..`P3` | 显式优先级；缺省由 `capability + failure_count` 映射 |
| `cases[].payload` | object | 默认 `{}` | 评测平台原始报告片段 |
| `limit` | int | 1..10000，**默认 200** | **单 source 每小时受理上限**（超出判 `RATE_LIMITED`） |
| `conversion` | object? | — | `{sample_set_id, schema_id}`；缺省则不转任务，案例停在 `RECEIVED` |

响应 `BackflowIngestOut`：

| 字段 | 说明 |
|---|---|
| `accepted` | 受理（将落库/转任务）的案例数 |
| `duplicated` | 重复跳过的案例数 |
| `rate_limited` | 超限拒绝的案例数 |
| `task_id` | 转换成功时的任务 ID；未配置 `conversion` 时为 `null`（**见 §6 已知问题**） |
| `decisions[]` | 与入参顺序一致的逐条判定：`decision` ∈ `CREATE_TASK` / `DUPLICATE_SKIPPED` / `RATE_LIMITED`，附 `reason` 与 `suggested_priority` |

### 2.2 案例列表（运营视图）

```
GET /api/v1/projects/{project_id}/annotation-backflow?limit=50
```

- `limit`：1..200，默认 50；按 `received_at` 倒序。
- 返回 `items[]`：`case_id` / `source` / `case_ref` / `capability` / `status`
  （`RECEIVED` / `CONVERTED`）/ `suggested_priority` / `failure_summary` /
  `task_id` / `received_at`。
- **权限是 owner 口径**：QA lead（editor）看不到列表，需 owner 或平台管理员代查。

---

## 3. 受理流程（SOP）

### Step 1：接入前自检（评测平台侧 / QA lead）

- [ ] `cases` 不超过 200 条；超过要**分批回调**（单批上限，不是限流值）；
- [ ] `source` 稳定（同一评测系统固定一个值，否则每换一次都等于新桶）；
- [ ] `case_ref` 在来源系统内稳定唯一（重发必须保持同一值，否则去重失效）；
- [ ] 需要自动落任务时，确认 `sample_set_id` / `schema_id` 是**本项目已存在**的配置；
- [ ] 明确本次上限 `limit`（默认 200/小时/source），风暴期不要靠调大 `limit` 硬扛。

### Step 2：发送受理请求（可复制 curl）

```bash
BASE=http://127.0.0.1:8000/api/v1
AUTH="Authorization: Bearer ${BEARER_JWT}"
PROJECT_ID="<project-uuid>"
SAMPLE_SET_ID="<sample-set-uuid>"   # 需要受理即转任务时填
SCHEMA_ID="<schema-uuid>"           # 同上

curl -sS -X POST "${BASE}/projects/${PROJECT_ID}/annotation-backflow" \
  -H "${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{
    "cases": [
      {
        "source": "eval-platform-a",
        "case_ref": "run-20261126-0001/case-17",
        "capability": "manipulation.pick",
        "failure_summary": "抓取后滑落，目标偏移 >5cm",
        "failure_count": 3,
        "payload": {"report": "eval-2026-11-26.json", "score": 0.41}
      }
    ],
    "limit": 200,
    "conversion": {"sample_set_id": "'"${SAMPLE_SET_ID}"'", "schema_id": "'"${SCHEMA_ID}"'"}
  }'
```

不带转换（只受理、后置处理不在本版提供，见 §6）：

```bash
curl -sS -X POST "${BASE}/projects/${PROJECT_ID}/annotation-backflow" \
  -H "${AUTH}" -H "Content-Type: application/json" \
  -d '{"cases":[{"source":"eval-platform-a","case_ref":"run-20261126-0002/case-3","capability":"navigation.cross_room"}],"limit":200}'
```

### Step 3：读结果并按判定分流

| 响应信号 | 含义 | 运营动作 |
|---|---|---|
| `accepted > 0` 且 `task_id` 非空 | 已受理并转任务 | 在标注任务列表核对 `code=BACKFLOW-*`，进入排期 |
| `accepted > 0` 且 `task_id=null` | 已受理、未转任务 | 案例可查（`RECEIVED`），但**本版无事后转换 HTTP 端点**，见 §6 |
| `duplicated > 0` | 同 `(source, case_ref)` 已受理过 | **正常幂等**，不报错、不重试；核对回调侧重试风暴 |
| `rate_limited > 0` | 该 source 本小时已触上限 | 走 §4 限流处置 |
| HTTP 409 `duplicate_resource` | 并发双写撞唯一键，**整批未写入** | 安全重试同一批（不会半写） |
| HTTP 422 `validation_failed` | 字段缺失/超长/`cases` 为空或 >200 | 修正载荷后重发 |

---

## 4. 限流处置（RATE_LIMITED）

### 4.1 语义

- 窗口**固定 1 小时**（`RATE_WINDOW = timedelta(hours=1)`，领域常量，不随配置变化）；
- 统计基数 = 该 source 在**本项目**最近 1 小时已受理（`RECEIVED` + `CONVERTED`）的案例数；
- 上限来自**本次请求体的 `limit`**（默认 200，范围 1..10000）。
  ⚠️ 配置层目前**没有**独立的回流限流环境变量（grep `backend/app/core/config.py` 无
  backflow 项），限流上限只由**请求体 `limit`** 控制，不要臆造不存在的环境变量开关；
- 超限是**部分拒绝**：超出的案例判 `RATE_LIMITED`，同批未超限的照常受理，
  HTTP 仍为 200——**不能只看 HTTP 状态码，必须看 `rate_limited` 计数**；
- `RATE_LIMITED` 判定不落库，重发时不会被"记住"，只看窗口内已受理数。

### 4.2 处置顺序（QA lead）

1. **确认来源**：读 `decisions[]` 里 `decision=RATE_LIMITED` 的 `source`，区分是单
   评测平台风暴还是多个来源叠加；
2. **不要放大 `limit`**：调大上限等于让风暴直接冲垮标注队列，属于最后手段且需 owner 批准；
3. **削峰**：让评测平台按 `source` + 时间分批回调；把非 P0/P1 案例顺延到下一小时窗口；
4. **提优先级**：把 `failure_count` 高、`capability` 关键的案例前置，保证窗口额度用在刀刃上；
5. **升级**：持续 3 个窗口仍 `rate_limited > 0`，升级 owner 评估是否临时提高 `limit`
   或调整评测侧回调频率，并在变更记录留证。

---

## 5. 重发幂等语义（回调方必读）

三层去重，语义统一为「重复即幂等成功」：

| 层 | 机制 | 表现 |
|---|---|---|
| 1 | 批次内去重（纯函数） | 同一批里重复的 `(source, case_ref)` 只受理一次，其余计 `duplicated` |
| 2 | 批次间预查询（`known_refs`） | 已存在（`RECEIVED`/`CONVERTED`）→ `DUPLICATE_SKIPPED`，HTTP 200 |
| 3 | 数据库唯一键 `(source, case_ref)` 兜底 | 并发双写撞键 → **整批回滚**，HTTP 409 `duplicate_resource`，响应提示「整批未写入；请重试」 |

运营要点：

- **重复不是错误**：`duplicated > 0` 时不要重试、不要告警风暴，核对回调侧是否在
  无效重试即可；
- **409 时可以整批重试**：因为整批回滚，不会产生半写；
- **同一 `case_ref` 改了 `capability` 也不会更新**：已受理行不会被覆盖，需要修正
  请用新的 `case_ref` 重新回调；
- 幂等键是 `(source, case_ref)`，数据库唯一约束是**表级**（`uq_annotation_backflow_cases_source_case_ref`，
  不含 `project_id`）——因此**跨项目复用同一 `source + case_ref` 会撞唯一键并返回 409**；
  多项目共用同一个评测平台时，必须在 `source` 里带项目前缀或保证 `case_ref` 全局唯一。
  同项目内换 `source` 等于新案例。

---

## 6. 转换时机与已知限制

### 6.1 转换时机（唯一 HTTP 路径）

- **受理时随批转换**：`POST .../annotation-backflow` 带 `conversion` 块，同批受理的
  案例合并为 1 个标注任务，状态置 `CONVERTED` 并回填 `task_id`；
- **转换是可选的**：不带 `conversion` 时案例停在 `RECEIVED`，保留"运营后置决定"的空间；
- 转换目标 `sample_set_id` / `schema_id` 必须是**项目侧既有配置**：回调方只能引用，
  不能新建或编造；调用方需具备项目写权限（`get_write_scope`）。

### 6.2 已知限制（MISSING，勿按已具备操作）

| 限制 | 证据 | 运营影响 |
|---|---|---|
| **没有事后转换 HTTP 端点** | `grep -rn "convert" backend/app/api/v1/` 只有 `gap_candidate_routes.py`；`BackflowUseCases.convert()` 未被路由引用 | 已停在 `RECEIVED` 的案例，本版无法通过 Web/API 再转任务。**需要转任务的批次必须在受理时就带 `conversion`** |
| `task_id` 不保证指向本批任务 | 路由用 `select(task_id).where(status="CONVERTED").first()`，未按批次过滤/排序 | 多批次并存时响应里的 `task_id` 可能不是本批任务；以 `accepted` 计数与任务列表 `code=BACKFLOW-*` 核对为准 |
| 列表读权限为 owner 口径 | `GET` 用 `get_owner_scope` | QA lead 无法自助查列表，需 owner/平台管理员导出 |

> 若业务需要"先受理、后按需转换"，应在同批 PR 里新增一个事后转换路由（`POST` 子路径，
> 与 `paths.ops.yaml` 契约同步），而不是由运营手工改库。本文不写不存在的路径名，
> 避免照抄 404。

---

## 7. 责任与时限（运营约定）

| 环节 | 责任角色 | 时限 |
|---|---|---|
| 回调受理与结果核对 | QA lead | 收到回调后 **1 个工作日内** |
| 限流风暴处置 | QA lead | **2 小时内**给出削峰方案 |
| 持续限流升级决策 | 项目 owner | **4 小时内**响应 |
| 转换目标（sample_set/schema）配置 | 项目 owner | 受理前必须就绪 |
| 失败案例回流闭环复盘 | QA lead + owner | 每周一次 |

---

## 8. 验证命令（写文档时已执行的 grep）

```bash
grep -rn "annotation-backflow" backend/app/api/v1/
grep -n "DEFAULT_HOURLY_LIMIT\|RATE_WINDOW\|MAX_BATCH_SIZE" backend/app/domain/annotation/backflow.py
grep -n "class BackflowIngestIn\|class BackflowCaseIn\|limit: int\|conversion" backend/app/api/v1/annotation_backflow_routes.py
grep -rn "backflow" backend/app/core/config.py   # 预期：无输出（无独立环境变量）
```
