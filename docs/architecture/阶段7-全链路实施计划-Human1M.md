# 阶段 7 全链路实施计划：NVIDIA PhysicalAI-Human-1M-100K

> 依据：用户指令「数据全链路需跑通，拉取 hugging face 第一人称数据，英伟达最新发布的那个」。
> 目标：用**真实公开数据**跑通 `登记 → 导入 → 校验 → 解析 → 审看 → 人工审核 → 质量报告` 全链路。

## 1. 数据源事实（2026-11-15 核实）

| 项 | 值 | 来源 |
|---|---|---|
| Repo | `nvidia/PhysicalAI-Human-1M-100K` | HF API |
| Commit sha | `7e312e654818d1e0d3ebec3b78e50ee833b429f8` | HF API `sha` |
| 许可证 | **CC-BY-4.0** | `cardData.license` |
| Gated | `false` | HF API |
| 公开范围 | `preview/`：15 demo / **39 个视频** / 15 种任务 | `preview/metadata.jsonl` |
| 全量 | `full_dataset_included: false`，需授权 | `HUGGING_FACE_LINKS.json` |
| 格式 | **LeRobot v3.0**：Parquet（data+meta）+ 分相机 H.264 MP4 | `data/schema.json` |
| 视频编码 | H.264 / 720×406 / 30fps / 12s 片段 | `ffprobe` 实测 |
| 机位 | main 15 / right 7 / stereo 7 / wrist_l 5 / wrist_r 5 | metadata |

**关键约束**：全量 100K 需 NVIDIA 授权，公开可访问的只有 `preview/` 的 39 个视频。
这恰好符合 `docs/ddp/07`「不默认全量下载」。本计划用 39 个真实视频跑通链路，
不伪造数据、不假装拥有全量权限。

## 2. 链路分解与现状

| 步 | 能力 | 现状 | 本计划 |
|---|---|---|---|
| 1 | 数据源登记 | ✅ 已实现（阶段 7 第一批） | 登记该 repo + sha + CC-BY-4.0 |
| 2 | 导入任务登记 | ✅ 已实现 | 建导入任务 |
| 3 | **实际下载与缓存** | ❌ MISSING | **新增**：worker 消费事件，流式下载 preview |
| 4 | **文件校验** | ⚠️ 上传链路有，未接入导入 | **新增**：类型/大小/sha256/ffprobe 探测 |
| 5 | **LeRobot 元数据解析** | ❌ MISSING | **新增**：metadata.jsonl → Episode/MediaAsset |
| 6 | **视频审看** | ❌ MISSING（ADR-0017 已选型） | **新增**：原生 video 播放器 + 时间轴 |
| 7 | **人工审核** | ⚠️ 标注域有职责分离模式 | **新增**：复用模式，面向原始数据 |
| 8 | **质量报告** | ⚠️ Dataset 域有 | **新增**：原始数据维度 |

## 3. 交付批次

### 批次 A：导入 worker（下载 + 校验）
- `data-platform/ddp_data/huggingface/importer.py`：流式下载 + sha256 + 内容签名
- `backend/app/workers/datasources.py`：消费 `datasource.import_requested`
- 落库：文件清单（对象存储 key、大小、sha256、MIME、ffprobe 元数据）
- 状态机已就绪（PENDING→RUNNING→SUCCEEDED/FAILED）

### 批次 B：LeRobot 解析
- `data-platform/ddp_data/lerobot/parser.py`：metadata.jsonl → Canonical Episode
- 字段映射：`example_id`→Episode、`file_name`→MediaAsset、`view`→机位、`excerpt_*`→时间轴
- 不臆造：元数据没有的字段（如机器人位姿）保留为空，不补齐

### 批次 C：视频审看前端
- 原生 `<video>` + 自定义时间轴（ADR-0017）
- 多机位切换、片段定位（用 `excerpt_start_seconds`）
- 预签名 URL（服务端签发，前端不拼路径）

### 批次 D：人工审核 + 质量报告
- 复用标注域的职责分离（登记人 ≠ 审核人）
- 质量规则：视频可解码、时长匹配、sha256 一致、机位齐全

## 4. 诚实边界

| 项 | 说明 |
|---|---|
| 不做全量下载 | 需 NVIDIA 授权；且 100K 小时不可行 |
| 不做 ROS Bag / MCAP 解析 | 该数据集不含；等有真实来源再做 |
| 不做自动标注 | 本批只到人工审核 |
| Parquet 数据解析 | preview 只有视频 + jsonl，无 parquet 样本；解析器留接口但不硬造 |
| 视频播放器 | 原生 video，不引入 hls.js（MP4 无需 HLS） |

## 5. 验收标准

- [ ] 从 HF 真实下载 39 个预览视频到 MinIO，sha256 可校验
- [ ] 解析出 15 个 Episode、39 个 MediaAsset，机位/任务/时长与 metadata 一致
- [ ] 前端能播放每个视频并定位时间轴
- [ ] 人工审核可提交结论，登记人不能审自己
- [ ] 质量报告说明样本范围、规则版本、通过率
- [ ] 全链路有端到端脚本与真实运行证据
