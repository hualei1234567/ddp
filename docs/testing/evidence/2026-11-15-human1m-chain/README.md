# NVIDIA Human-1M 全链路验收报告（2026-11-15）

> 证据目录：`docs/testing/evidence/2026-11-15-human1m-chain/run.log`
> 复现：`backend/.venv/bin/python scripts/e2e/human1m-chain.py`

## 1. 验收结论

**用真实公开数据跑通了 `docs/ddp/03` §3 定义的全链路前 8 步。**
无 mock、无伪造：真实 Hugging Face 下载、真实 PostgreSQL 16、真实 MinIO。

| 步 | 能力 | 结果 | 证据 |
|---|---|---|---|
| 1 | 数据源登记 | ✅ | repo `nvidia/PhysicalAI-Human-1M-100K`，commit `7e312e65…`，CC-BY-4.0 |
| 2 | 导入任务登记 | ✅ | 状态 PENDING → RUNNING → SUCCEEDED |
| 3 | 真实下载与校验 | ✅ | 39 个视频，46,335,568 字节，逐个 sha256 + MP4 `ftyp` 签名校验 |
| 4 | LeRobot 解析 | ✅ | 15 Episode / 39 MediaAsset，5 种机位 |
| 5 | 对象存储 | ✅ | MinIO 真实对象，回读校验 `ftypisom` |
| 6 | 视频审看能力 | ✅ | 预签名 URL + **HTTP 206 Range**（浏览器可 seek） |
| 7 | 人工审核 | ✅ | 独立审核人 ACCEPT → APPROVED；提交人自审被拒（422） |
| 8 | 质量报告 | ✅ | 规则 v1.0.0，6 条全部 PASS |

## 2. 数据源事实

| 项 | 值 |
|---|---|
| Repo | `nvidia/PhysicalAI-Human-1M-100K` |
| Commit sha | `7e312e654818d1e0d3ebec3b78e50ee833b429f8` |
| 许可证 | **CC-BY-4.0**（可商用） |
| Gated | `false` |
| 公开范围 | `preview/`：15 demo、39 个视频、15 种真实任务 |
| 全量 100K | **需 NVIDIA 授权**（`full_dataset_included: false`） |
| 格式 | LeRobot v3.0；预览为 Parquet 元数据 + H.264 MP4 |
| 视频 | H.264 / 720×406 / 30fps / 12 秒片段（`ffprobe` 实测） |

任务样例：缝蕾丝、包饺子、修摩托刹车、打磨牙冠、焊接电路板、捆玻璃瓶、
修剪黄瓜藤、给奶酪轮涂层贴标——都是真实的第一人称人类操作。

## 3. 真实下载明细

```
episodes=15  assets=39  bytes=46,335,568
```

机位分布（与 `metadata.jsonl` 完全一致）：
- main 15 / right 7 / stereo 7 / left wrist 5 / right wrist 5

对象存储键形如：
```
data-sources/{project_id}/{data_source_id}/{revision}/videos/demo_001_0b4a67b0_main.mp4
```
**键里带 revision**：同一数据源换版本不会覆盖旧对象。

## 4. 质量报告（真实数据）

| 规则 | 结果 | 说明 |
|---|---|---|
| `assets_present` | PASS | 共 39 条媒体资产 |
| `asset_size_positive` | PASS | 全部资产大小为正 |
| `asset_sha256_valid` | PASS | 全部校验和格式合法 |
| `episodes_have_assets` | PASS | 15 个片段均有媒体资产 |
| `main_camera_present` | PASS | 机位集合完整 |
| `timing_fields_valid` | PASS | 片段起点/时长/帧率合法 |

规则版本 `1.0.0` 与样本范围 `SAMPLE` 都写在报告里——`docs/ddp/07` §7 要求
报告必须能说明"用什么规则、评了哪些样本"，只给一个总分是不够的。

## 5. 人工审核与职责分离

- 导入提交人：`editor` 角色
- 审核人：`owner` 角色，**与提交人不同**
- 提交人自审 → **422 被拒**（应用层 + 数据库 CHECK 双重拦截）
- 审核人 ACCEPT → `APPROVED`，reason 记录在案
- 终态再裁决 → 被拒

## 6. 播放能力（ADR-0017 落地）

| 验证项 | 结果 |
|---|---|
| 预签名 URL 含 SigV4 签名参数 | ✅ |
| URL 有效期 | 900 秒（审看是交互行为，短有效期限制泄漏面） |
| `curl -r 0-1023` Range 请求 | ✅ **HTTP 206**，1024 字节 |
| 返回内容首字节 | `ftypisom`（有效 MP4） |
| 前端播放器 | 原生 `<video>`，不自研内核 |

**206 而不是 200 是硬指标**：浏览器拖动进度条依赖 Range 支持；
只返回 200 的视频无法 seek。

## 7. 测试与门禁

| 项 | 结果 |
|---|---|
| 后端（SQLite） | 872 passed / 18 skipped |
| 后端（真实 PostgreSQL 16） | 全部通过，含 PG 守卫用例 |
| 前端 | 333 passed / 19 skipped |
| `make check` | 全绿（ruff + pytest + tsc + eslint + build + secret） |
| 迁移链 | 0001 → 0026，0023–0026 在真实 PG 升降级通过 |

## 8. 明确的边界（未做的部分）

| 项 | 原因 |
|---|---|
| 全量 100K 下载 | 需 NVIDIA 授权，且 100K 小时不可行 |
| Parquet 数据解析 | 预览数据无 parquet 样本；不凭 schema 文档硬造解析器 |
| ROS Bag / MCAP | 该数据集不含；等有真实来源 |
| 自动标注 | 本批只到人工审核 |
| 审核/质量报告前端页面 | 后端能力已就绪，前端按增量迭代排后续 |
| 定时备份任务 | 阶段 8 工程化项 |

## 9. 复现步骤

```bash
docker compose up -d postgres minio
cd backend && .venv/bin/alembic upgrade head && cd ..
backend/.venv/bin/python scripts/e2e/human1m-chain.py
```

脚本会真实下载 46MB 数据（首次约 1–2 分钟），重复运行会累积对象，
可用 `docker compose down -v` 清理。
