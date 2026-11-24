### MEM-INC-010 | 区分「实际读取行数」与「数据集总行数」

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `数据物化/分块读取` | 批次/资产 `全部受限读取路径` | 密级 `INTERNAL`
- **核心结论**：分块/限长读取时，``payload.length``（本次读到几行）与 ``payload.shape[0]``（数据集总共几行）**是两个不同的数**。用 `shape[0]` 去迭代会把**未读取的行也当成有数据**，产出比预期多很多的记录。任何「限长读取 + 按行迭代」都必须用实际读取行数。
- **关键依据与细节**：
  - 真实 bug（V51-002 实现时）：`extract_robomimic_demo(max_frames=7)` 对一个 20 帧的 demo，本应产出 7 条记录，实际产出 **20 条**
  - 根因：`rows = _row_count(shape)` 用的是数据集完整 shape；而 `read_slice` 只读了 `length=7`
  - 修复：`_safe_slice` 返回 `payload.length`（实际读取行数），迭代用它
  - **为什么危险**：多出的记录不是空的——它们是从"没读到的数据"里凭空产生的索引，
    下游会以为有 20 帧真实数据，实际只有 7 帧被读取。这是**静默的数据伪造**
  - 同类风险：任何 `for i in range(shape[0])` 配分块读取的地方都需复查
- **影响与后续动作**：
  - 分块读取 API 必须同时返回「读取范围」与「实际行数」，调用方不许自己用 shape 推导
  - 已加专项回归测试 `test_max_frames_limit_uses_actual_read_rows`
  - 责任人：数据平台；**已修复并加测试**

---

#### 工程扩展字段

- **check_ref**：`backend/tests/unit/test_stream_extraction_stage.py::test_max_frames_limit_uses_actual_read_rows`
- **recurrence**：`1`
- **tags**：`chunked-read,slice,shape,off-by-n,data-fabrication,hdf5`
