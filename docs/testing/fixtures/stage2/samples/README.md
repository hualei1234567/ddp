# DDP 阶段 2 合成样例包

本目录由 `generate_stage2_samples.py` 生成，仅供测试使用。

- 数据来源：`[SYNTHETIC]`，不是真实采集数据；
- MP4/MCAP/HDF5/PARQUET 为签名容器样例，不可用于真实解码/解析验收；
- 负向样本只允许在隔离环境使用，不得进入真实数据目录或生产桶；
- 逐文件 SHA256 见 `../samples.manifest.json` 与 `../SHA256SUMS`；
- 复现命令：`backend/.venv/bin/python docs/testing/fixtures/stage2/generate_stage2_samples.py`。
