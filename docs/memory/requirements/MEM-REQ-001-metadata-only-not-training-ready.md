### MEM-REQ-001 | METADATA_ONLY 不等于训练就绪，状态必须分层

- **类型与状态**：`REQ` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `Dataset/训练交付` | 批次/资产 `全部 LE_ROBOT 导出` | 密级 `INTERNAL`
- **核心结论**：`lerobot==0.3.3` 官方 Loader 对产物的**元数据构造通过**，只能证明 "metadata 可加载"，**不能**证明"逐帧训练数据可读"。必须用**分层状态**区分，且不允许上层状态覆盖下层证据。
- **关键依据与细节**：
  - 实测（E1）：`ds[0]` 返回 6 个键 `episode_index/task_index/length/timestamp/split/task`，**无 observation、无 action**
  - 导出 `features` 仅 5 项；每 Episode `length=1`、`fps=1`（标称值）
  - 三层状态口径：
    - `VERIFIED_BY_LOADER_METADATA` —— 仅元数据
    - `VERIFIED_BY_LOADER_FRAME` —— 逐帧数据读取通过
    - `VERIFIED_FOR_TRAINING_PIPELINE` —— 目标训练代码实际消费通过
    - `TRAINING_HANDOFF_VERIFIED` —— 外部平台确认接收
  - 当前产物标注：`loadable_scope="METADATA_ONLY"`、`frame_level_read="NOT_CLAIMED_METADATA_ONLY"`、`fps_source="NOMINAL_METADATA_ONLY"`
- **影响与后续动作**：
  - **禁止**把 `METADATA_ONLY` 改名或向上覆盖为"训练就绪"
  - UI 与 API 必须明确区分这些状态
  - 升级到 FRAME 需要真实逐帧数据（observation/action feature 真实存在）
  - 责任人：产品+数据平台；**边界已守住**

---

#### 工程扩展字段

- **check_ref**：`backend/app/domain/datasets/exporting.py`（状态枚举）+ `docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md`
- **recurrence**：`1`
- **tags**：`lerobot,metadata-only,frame-level,training-ready,status,claim-boundary`
