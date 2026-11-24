### MEM-SOP-001 | 真实样本准入检查清单（GATE-0）

- **类型与状态**：`SOP` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `数据物化/样本准入` | 批次/资产 `任何新格式的首次接入` | 密级 `INTERNAL`
- **核心结论**：判定「有真实样本」必须四步全过：**文件本体存在 → SHA256 与事实登记一致 → 能读出非空 payload → 结构符合声明**。仅看扩展名或只看 facts.json 存在，都不算真实样本。
- **关键依据与细节**：

  **四步检查（缺一不可）**
  ```bash
  # 1. 文件本体存在且大小与登记一致
  ls -l <sample>                                    # 对比 facts.json 的 file_size_bytes

  # 2. SHA256 与事实登记一致
  shasum -a 256 <sample>                            # 对比 facts.json 的 file_sha256

  # 3. 能读出非空 payload（不是只有魔数的空壳）
  xxd <sample> | head -5                            # 看到 SYNTHETIC_* 就立即判定为假

  # 4. 结构符合声明
  DDP_TEST_REAL_HDF5=<path> pytest backend/tests/unit/test_hdf5_real_public_sample.py -q
  ```

  **反面案例（真实发生）**
  - 仓库 `sample.h5` 166 字节，内容 `SYNTHETIC_HDF5_CONTAINER_ONLY` → 假样本
  - `facts.json` 存在但文件本体缺失 → 只能证明"曾有人登记过"，不能证明"现在能测"
  - 真实样本测试默认 `skipif`，`env | grep DDP_TEST` 无输出 → **从未跑过**

  **样本存放规则**
  - 大文件（>5MB）**不进仓库**，放仓库外，用环境变量指定（`DDP_TEST_REAL_HDF5`）
  - `facts.json` 进仓库：记录来源/SHA256/大小/结构观察/许可
  - 许可必须可用（MIT/Apache 等）；公开数据集只能标 `PUBLIC_DATASET_INTEGRATION_TESTED`，**不得标真实硬件 Pilot**

  **下载与校验（以 RoboMimic 为例）**
  ```bash
  mkdir -p ~/ddp-samples/robomimic && cd ~/ddp-samples/robomimic
  curl -L -o demo_v15.hdf5 \
    "https://huggingface.co/datasets/amandlek/robomimic/resolve/74fa018461f479cd9fd15b924a16103012096203/v1.5/lift/ph/demo_v15.hdf5"
  shasum -a 256 demo_v15.hdf5   # 期望 68f2a8cf…2f37
  ```

- **影响与后续动作**：
  - 任何「格式能力已支持」的宣称，必须先过本 SOP
  - 新格式接入的第一个 PR 必须包含本 SOP 的检查输出
  - 责任人：数据平台；**每次新样本接入执行**

---

#### 工程扩展字段

- **check_ref**：`backend/tests/unit/test_hdf5_real_public_sample.py`（需 DDP_TEST_REAL_HDF5）+ `docs/roadmap/v5.1/s0/V51_GATE0_Sample_Verification.md`
- **recurrence**：`1`
- **tags**：`sample,golden-path,sha256,gate,sop,fixture,hdf5`
