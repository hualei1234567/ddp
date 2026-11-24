### MEM-INC-004 | 合成容器头被当成真实样本

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `数据物化/样本准入` | 批次/资产 `data-platform 测试 fixture` | 密级 `INTERNAL`
- **核心结论**：仓库里名为 `sample.h5` / `sample.mcap` / `sample.parquet` 的文件**不是真实数据**，内容直接写着 `SYNTHETIC_*_CONTAINER_ONLY` 后跟递增字节。用它们做测试会误以为「格式支持已验证」，实际只验证了魔数识别。
- **关键依据与细节**：
  - `xxd` 实测：
    - `sample.h5`（166 B）：`8948 4446 0d0a 1a0a | "SYNTHETIC_HDF5_CONTAINER_ONLY"`
    - `sample.mcap`（102 B）：`894d 4341 5030 0d0a | "SYNTHETIC_MCAP_CONTAINER_ONLY"`
    - `sample.parquet`（105 B）：`5041 5231 | "SYNTHETIC_PARQUET_CONTAINER_ONLY"`
  - 真实公开样本（RoboMimic 36.7MB / MCAP testdata 5.4KB）**只登记了 facts.json（SHA256+结构），文件本体不在仓库**
  - 真实样本测试默认 `skipif`（需 `DDP_TEST_REAL_HDF5` 等环境变量），实测**从未在本地跑过**
  - 修复动作：GATE-0 主动下载 RoboMimic，SHA256 校验 `68f2a8cf…2f37` 匹配后才算真实样本
- **影响与后续动作**：
  - 判定"有真实样本"的标准必须是：**文件本体存在 + SHA256 与事实登记一致 + 能读出非空 payload**
  - 仅看扩展名 / 仅看 facts.json 存在，都不算
  - 新增格式能力声明前，必须先有一份 SHA256 校验通过的真实样本
  - 责任人：数据平台；**已建立 GATE-0 校验流程**

---

#### 工程扩展字段

- **check_ref**：`backend/tests/unit/test_hdf5_real_public_sample.py`（需 DDP_TEST_REAL_HDF5 才真跑）+ `docs/roadmap/v5.1/s0/V51_GATE0_Sample_Verification.md`
- **recurrence**：`1`
- **tags**：`sample,fixture,synthetic,hdf5,mcap,parquet,sha256,golden-path`
