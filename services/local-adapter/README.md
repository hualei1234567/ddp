# DDP 本地真实 Adapter（services/local-adapter）

这是一个**真实执行**的训练 / 推理 / 评测 Adapter，不是 Mock：

- 从 MinIO 读取 DDP 导出的数据集产物；
- 用 `ffmpeg` 从真实视频按时间均匀抽帧，缩放到 32×32 灰度；
- 用 scikit-learn `LogisticRegression` 训练帧级 action 分类器；
- 模型、评测报告、推理预测都真实写回 MinIO，并做回读校验；
- 每个阶段都用 HMAC-SHA256 回调 DDP，签名原文是**实际发送的原始 body 字节**。

它是 CPU 参考实现，用于把「训练 → 推理 → 评测」链路真实跑通，**不是机器人策略模型**。

---

## 1. 服务架构与端点

HTTP 线程只做参数校验与入队；训练/推理/评测在线程池中执行，单进程内存队列，
并发上限由 `LOCAL_ADAPTER_MAX_CONCURRENT_RUNS` 控制。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 健康检查、ffmpeg/队列/adapter 配置状态 |
| POST | `/training-runs` | DDP 提交训练，立即返回 `external_run_id` |
| GET | `/training-runs/{run_id}` | 查询训练运行；`status` 已映射为 DDP `ExternalTrainingStatus`，内部状态见 `internal_status` |
| POST | `/training-runs/{run_id}/cancel` | 请求取消 |
| POST | `/evaluations` | 提交评测 |
| POST | `/inference-runs` | 提交推理（本服务主路径） |
| POST | `/inference-tasks` | 同上，兼容 DDP 出站网关 `model_platform_http.py` 的路径 |
| GET | `/runs/{run_id}` | 通用运行详情（内部状态） |
| GET | `/runs` | 最近运行列表（本地运维观察） |

训练提交同时接受两种字段名：

- `training_delivery_id`：DDP `backend/app/infrastructure/training_adapter_http.py` 实际发送的字段；
- `delivery_id`：兼容旧调用/手工调试。

其余字段与 DDP 网关一致：`adapter_id`、`adapter_version`、`correlation_id`、
`dataset_version_id`、`config`。

模型提交字段：`adapter_id`、`adapter_version`、`evaluation_id`/`task_id`、
`model_version_id`、`dataset_version_id`、`service_id`、`episode_ids`、`config`。

---

## 2. 训练/推理/评测各自真实做了什么

### 2.1 训练

1. 定位导出前缀：
   - `config.export_prefix` 优先；
   - 否则 `exports/{project_id}/{dataset_version_id}/{export_job_id}/`；
   - 未给 project/export_job 时枚举对象存储，选择最近写入的 export job，并把
     `export_resolution` 写入运行记录与元数据。
2. 读取 `ddp/sidecar.json`（校验 `ddp.export.sidecar/*`、dataset/project 一致性）、
   `meta/episodes.jsonl`、`meta/ddp_divisions.json`。
3. 读取 `meta/media.jsonl`（每行至少要有 `episode_id` + `object_key`；支持
   `episode`/`key`/`s3_key`/`path` 等别名与嵌套 `videos` 列表）。**缺失即 BLOCKED**，
   不会编造训练结果。
4. 读取监督标签，按优先级：
   - `data/episodes.parquet`（`episode_id`/`split`/`action`[/`success`/`quality_score`]）；
   - `data/episodes.jsonl`（当前导出补全任务实际写出的形态，字段同上）；
   - 回退：`media.jsonl` 自带 action + `ddp_divisions.json` 给出 split。
   都缺失即 BLOCKED。
5. 对 train split 的每个 episode、每路视频：下载 → 校验 `sha256`（manifest 提供了才校验）
   → `ffmpeg` 均匀抽帧（默认每路 8 帧）→ 32×32 灰度原始像素。
6. 训练 `StandardScaler + LogisticRegression(C=0.1, solver="saga", class_weight="balanced")`。
   选择逻辑回归而非最近质心的原因：能输出多分类概率（推理需要 `confidence`），
   训练快、CPU 上确定、依赖成熟。
7. 产物写回 MinIO：
   - `models/{project_id}/{run_id}/model.joblib`
   - `models/{project_id}/{run_id}/training_metadata.json`
   两者都做回读字节比对；`sha256`/`size_bytes` 写入运行记录与回调 payload。

> 标签是 **episode 级 action**，被应用到该 episode 的所有帧。这是现有导出形态的
> 限制，不是帧级动作分割。

### 2.2 评测

- 定位同一导出产物，读取 test split（`config.split` 可覆盖，默认 `test`）。
- 加载训练产物（见 §4 的模型解析顺序），对每一帧真实 `predict_proba`。
- 计算 `accuracy`、`macro_f1`、`mean_confidence`、`test_samples`、`episode_count`、
  `class_count`，并在报告中写入 per-class precision/recall/f1、混淆矩阵。
- 写 `evaluations/{project_id}/{evaluation_id}/report.json` 并回读校验。
- 回调 `evaluation.completed`，`report_ref` 带 `object_key`/`sha256`/`size_bytes`。

### 2.3 推理

- 使用 `dataset_version_id`（或 `config.dataset_version_id`/`config.export_prefix`）
  定位导出；按 `episode_ids` 或 `config.split` 选择 episode。
- 加载模型后逐帧预测，写
  `inference/{project_id}/{task_id}/predictions.jsonl`，每行：
  `episode_id`、`camera`、`frame_index`、`timestamp_seconds`、`predicted_action`、
  `confidence`、`probabilities`。
- 回调 `inference.completed`，`result_ref` 带 `object_key`/`sha256`/`size_bytes`/`row_count`。

---

## 3. 回调协议

签名与 DDP 实现完全一致（`backend/app/domain/datasets/adapter_callback.py`、
`backend/app/domain/modelops/callback_security.py`）：

```text
X-DDP-Signature: sha256=<hex>
X-DDP-Timestamp: <unix 秒>
X-DDP-Nonce: <8-128 字符唯一值>

signature = HMAC_SHA256(secret, f"{timestamp}.{nonce}." + raw_body_bytes)
```

实现要点：

- body 只序列化一次，签名和发送使用同一份原始字节；
- 每次重试重新生成时间戳与 nonce，但 `event_id` 与 body 不变，DDP 的 event_id
  幂等判断成立；
- 训练回调可能早于 DDP 把外部引用落库，因此 `404/409/425/429/5xx` 会退避重试；
  `401/403/422` 等不可重试错误立即停止并记录；
- 训练状态：`RUNNING` → `SUCCEEDED`；失败/阻塞 → `FAILED`（带 `error_code`、
  `error_summary`）；取消 → `CANCELLED`；
- 模型事件：`evaluation.completed` / `evaluation.failed` /
  `inference.completed` / `inference.failed`。

回调地址：

- 训练：`{DDP_API_BASE_URL}/api/v1/integrations/training-adapters/{adapter_id}/callbacks`
- 模型：`{DDP_API_BASE_URL}/api/v1/integrations/model-adapters/{adapter_id}/callbacks`

---

## 4. DDP 传入的 `config` 与模型解析

训练（`ExternalTrainingReferenceCreate.config`，自由对象）推荐字段：

| 字段 | 说明 |
|---|---|
| `project_id` | 项目 UUID；不传则从导出 sidecar/前缀发现 |
| `export_job_id` | 指定 export job；不传则选最近写入 |
| `export_prefix` | 直接指定完整导出前缀（优先级最高） |
| `split` | 训练 split，默认 `train`；允许 `train`/`validation`/`all` |

评测/推理推荐字段：

| 字段 | 说明 |
|---|---|
| `project_id` | 项目 UUID；不传则从导出发现 |
| `export_job_id` / `export_prefix` | 同训练 |
| `model_object_key` | 模型产物完整 key（最可靠） |
| `training_run_id` / `model_id` | 本服务的训练 run id（`model_id` 形如 `local-adapter/train-...`） |
| `split` | 评测默认 `test`；推理默认全部 |

模型解析顺序：`config.model_object_key` → `config.model_ref.object_key` →
`config.training_run_id`/`model_id` → 项目下恰好只有一个 `model.joblib` 时回退。
多个模型时报 `model_artifact_ambiguous`，不猜。

**已知对接缺口**：

1. DDP 的 `EvaluationCreate` schema 目前没有 `config` 字段，
   `POST /api/v1/.../evaluations` 不会把 `model_object_key` 传到 adapter。因此多模型
   项目下评测会报 `model_artifact_ambiguous`；单模型项目会走
   `single_artifact_fallback`。此项需要在 DDP 侧补契约，属于 adapter 之外的工作，
   本服务如实报错、不静默选模型。
2. 模型回调 body 必须带 `project_id`，但 DDP 的评测/推理 submit 不传 `project_id`。
   本服务会从 `dataset_version_id` 对应的导出 sidecar 发现项目；如果连导出都定位失败
   （例如 dataset_version_id 非法）且 `config.project_id` 也没有，则无法构造合法回调，
   只会在本地运行记录里记 `project_id_unknown`，不发送伪造 project_id。

---

## 5. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DDP_API_BASE_URL` | `http://localhost:8000` | DDP API 根地址（compose 内为 `http://api:8000`） |
| `LOCAL_ADAPTER_PUBLIC_BASE_URL` | `http://localhost:9100` | 返回给 DDP 的 `external_run_url` 前缀 |
| `LOCAL_ADAPTER_TRAINING_ADAPTER_ID` | `local-training` | 训练 adapter id |
| `LOCAL_ADAPTER_TRAINING_ADAPTER_VERSION` | `1.0.0` | 训练 adapter version |
| `LOCAL_ADAPTER_TRAINING_SECRET` | 无（必填） | 训练回调共享密钥，必须与 DDP 一致 |
| `LOCAL_ADAPTER_MODEL_ADAPTER_ID` | `local-model` | 模型 adapter id |
| `LOCAL_ADAPTER_MODEL_ADAPTER_VERSION` | `1.0.0` | 模型 adapter version（DDP 当前固定发 `1.0.0`） |
| `LOCAL_ADAPTER_MODEL_SECRET` | 无（必填） | 模型回调共享密钥 |
| `DDP_S3_ENDPOINT` / `DDP_S3_ACCESS_KEY` / `DDP_S3_SECRET_KEY` / `DDP_S3_BUCKET` / `DDP_S3_REGION` | 与 backend 相同 | 对象存储 |
| `LOCAL_ADAPTER_FFMPEG_PATH` | `/opt/homebrew/bin/ffmpeg` | ffmpeg 可执行文件（镜像内 `/usr/bin/ffmpeg`） |
| `LOCAL_ADAPTER_FFPROBE_PATH` | `/opt/homebrew/bin/ffprobe` | ffprobe |
| `LOCAL_ADAPTER_FRAMES_PER_VIDEO` | `8` | 每路视频抽帧数 |
| `LOCAL_ADAPTER_IMAGE_SIZE` | `32` | 灰度边长 |
| `LOCAL_ADAPTER_MAX_FRAMES` | `200000` | 单次任务帧预算，超限拒绝 |
| `LOCAL_ADAPTER_MAX_CONCURRENT_RUNS` | `2` | 线程池大小 |
| `LOCAL_ADAPTER_MAX_PENDING_RUNS` | `32` | 队列容量，满时提交返回 429 |
| `LOCAL_ADAPTER_MAX_VIDEO_BYTES` | `536870912` | 单视频大小上限 |
| `LOCAL_ADAPTER_RUN_TIMEOUT_SECONDS` | `1800` | 单任务超时 |
| `LOCAL_ADAPTER_FFMPEG_TIMEOUT_SECONDS` | `120` | 单次 ffmpeg/ffprobe 超时 |
| `LOCAL_ADAPTER_HTTP_TIMEOUT_SECONDS` | `10` | 回调 HTTP 超时 |
| `LOCAL_ADAPTER_CALLBACK_MAX_ATTEMPTS` | `6` | 回调最大尝试次数 |
| `LOCAL_ADAPTER_CALLBACK_INITIAL_DELAY_SECONDS` | `1` | 退避初始值 |
| `LOCAL_ADAPTER_CALLBACK_MAX_DELAY_SECONDS` | `30` | 退避上限 |
| `LOCAL_ADAPTER_ALLOW_SINGLE_MODEL_FALLBACK` | `true` | 允许"项目下只有一个模型"时回退 |
| `LOCAL_ADAPTER_EXPORT_PREFIX` / `MODEL_PREFIX` / `EVALUATION_PREFIX` / `INFERENCE_PREFIX` | `exports` / `models` / `evaluations` / `inference` | 对象前缀 |

`LOCAL_ADAPTER_TRAINING_SECRET` 或 `LOCAL_ADAPTER_MODEL_SECRET` 为空时服务**拒绝启动**，
避免"服务看似健康但回调必然 401"。

---

## 6. 启动方式

### 6.1 本地（已装 ffmpeg）

```bash
cd services/local-adapter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

DDP_API_BASE_URL=http://127.0.0.1:8000 \
DDP_S3_ENDPOINT=http://127.0.0.1:9000 \
LOCAL_ADAPTER_TRAINING_SECRET=local-training-dev-secret-0123456789abcdef \
LOCAL_ADAPTER_MODEL_SECRET=local-model-dev-secret-0123456789abcdef \
LOCAL_ADAPTER_FFMPEG_PATH=/opt/homebrew/bin/ffmpeg \
LOCAL_ADAPTER_FFPROBE_PATH=/opt/homebrew/bin/ffprobe \
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9100
```

### 6.2 Docker Compose

`.env.example` 已包含 `local-adapter` 与 DDP 出站网关所需配置。把相关段落合并进
`.env` 后：

```bash
docker compose --profile app up -d local-adapter
```

DDP 侧要能调用本服务，必须同时配置：

```env
DDP_TRAINING_ADAPTER_ENDPOINTS={"local-training":"http://local-adapter:9100"}
DDP_MODEL_ADAPTER_ENDPOINTS={"local-model":"http://local-adapter:9100"}
DDP_TRAINING_ADAPTER_SHARED_SECRETS={"local-training":"<与 LOCAL_ADAPTER_TRAINING_SECRET 一致>"}
DDP_MODEL_ADAPTER_SHARED_SECRETS={"local-model":"<与 LOCAL_ADAPTER_MODEL_SECRET 一致>"}
DDP_TRAINING_ADAPTER_CAPABILITIES={"local-training":["submit","status","cancel","callback","model_ref"]}
DDP_MODEL_ADAPTER_CAPABILITIES={"local-model":["evaluate","inference","callback","status","cancel"]}
```

---

## 7. 失败码

| `error_code` | 含义 |
|---|---|
| `blocked_media_manifest_missing` | 导出缺 `meta/media.jsonl`，无法定位视频 |
| `blocked_media_manifest_empty` | media.jsonl 存在但没有有效 episode+object_key |
| `blocked_labels_missing` | 既无 parquet/jsonl 标注，media 也没有 action |
| `blocked_insufficient_classes` | 训练集 action 类别少于 2 |
| `blocked_no_media_for_split` | 指定 split 下没有带标注的视频 |
| `blocked_no_media_episodes` | 指定 episode 没有可用视频 |
| `blocked_frame_budget_exceeded` | 抽帧数超过上限 |
| `media_sha256_mismatch` | 视频内容与 media.jsonl 的 sha256 不符 |
| `ffmpeg_unavailable` / `ffmpeg_failed` / `ffmpeg_timeout` | 抽帧失败 |
| `model_artifact_not_found` / `model_artifact_ambiguous` / `model_artifact_invalid` | 模型解析/加载失败 |
| `dataset_location_required` | 推理未提供数据位置 |
| `export_not_found` / `export_*` | 导出定位或元数据校验失败 |
| `job_cancelled` / `run_timeout` | 取消/超时 |
| `adapter_internal_error` | 未预期异常（日志有堆栈，回调只带简短摘要） |

---

## 8. 依赖与许可证

全部固定版本，见 `requirements.txt`。

| 依赖 | 版本 | 许可证 |
|---|---|---|
| fastapi | 0.115.6 | MIT |
| uvicorn | 0.34.0 | BSD-3-Clause |
| pydantic | 2.10.4 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| boto3 / botocore | 1.35.90 | Apache-2.0 |
| scikit-learn | 1.6.0 | BSD-3-Clause |
| scipy | 1.14.1 | BSD-3-Clause |
| numpy | 2.2.1 | BSD-3-Clause |
| joblib | 1.4.2 | BSD-3-Clause |
| pyarrow | 18.1.0 | Apache-2.0 |
| pytest | 8.3.4 | MIT |
| ffmpeg（Debian 包，镜像内安装） | 发行版固定 | Debian `ffmpeg` 二进制为 GPL-2+（含 GPL 组件）；libav* 核心为 LGPL-2.1+ |
| Python | 3.11 | PSF-2.0 |

未新增任何业务逻辑复制；回调签名、S3 访问、HTTP、训练算法均使用成熟库实现。

---

## 9. 测试

```bash
cd services/local-adapter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

测试覆盖：

- HMAC 签名与 DDP 算法逐字节一致；
- 训练/模型回调 body 字段形状、`model_ref` 白名单、失败 error 字段；
- HTTP 回调对原始 body 签名、503 重试、404 注册竞态重试、4xx 不重试；
- 无 media → BLOCKED；有 media 无标签 → BLOCKED；
- 训练 → 评测 → 推理全链路（替换 ffmpeg 解码边界，其余真实执行）；
- FastAPI `/health`、后台提交、DDP 状态映射、`training_delivery_id` 与
  `/inference-tasks` 契约别名。

---

## 10. 能力边界（明确不能做的部分）

- **不是机器人策略模型**：只做帧级 action 分类，不输出动作序列/关节控制，不接机器人。
- 标签是 episode 级，整段视频共用同一个 action；没有帧级动作分割或时序建模。
- 不做场景泛化保证；指标只反映当前导出数据与标签质量。
- 不做多机位时间同步、传感器融合、视频解码之外的多模态对齐。
- 不做流式/实时推理；推理是离线批处理。
- 单进程内存队列：进程重启后运行记录、取消状态、回调重试状态丢失；没有持久化
  任务队列与死信队列。生产化需要外部队列/数据库。
- 不支持多副本水平扩展（每个副本有独立内存队列）。
- 不宣称兼容 LeRobot 帧级格式；导出 sidecar 本身仍是 `frame_level_mapping=false`。
- 不在 HTTP 请求线程做长任务；但 Python 线程仍受 GIL 影响，超大训练集应改为进程池/
  外部训练平台。
- **安全**：`joblib` 反序列化会执行 pickle。本服务只应加载自己写出的
  `models/.../model.joblib`；不要让不可信对象写入模型前缀。
