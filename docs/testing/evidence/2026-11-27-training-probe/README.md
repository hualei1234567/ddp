# 训练侧消费闭环证据（阶段 5 / 蓝图 N4 终点）

## 审计问题

> Q1：导出的训练数据，**真的能被训练循环消费**吗？此前只验证过 `datasets` 库能
> **解析**制品结构（第三方 PASS），**从未真正训练**——这是数据飞轮最脆弱的一环。

## 探测对象

**真实导出制品**（现场用 `ddp_data.llm_export` 的 `export_sft` / `export_dpo` 生成）：
- `sft_messages.jsonl`：8 条具身智能操作步骤（读取文件/修改配置/运行测试）
- `preference_dpo.jsonl`：6 对偏好（正确执行 vs 跳过验证）

**训练循环**：numpy 实现的微型模型（环境无 torch）
- SFT：字符级 MLP 语言模型（前 4 字符 → 下一字符），交叉熵 + Adam
- DPO：简化 Bradley-Terry 偏好目标（线性打分）

## 实测结果（固定种子 20261127，可复现）

### SFT
| 指标 | 数值 |
|---|---|
| 初始 loss | **73.5495** |
| 最终 loss | **9.3581** |
| 是否收敛 | ✅（下降 87%） |
| 步数 | 2,640 |
| 词表大小 | 28 |
| 参数量 | 8,988 |

生成样本（模型实际输出，非原文照抄）：
```
第0步：运行测试并验证结果
第1步：运行测试并验证结果
第2步：运行测试并验证结果
```

### DPO
| 指标 | 数值 |
|---|---|
| 初始判对率 | **0.5** |
| 最终判对率 | **1.0** |
| 是否提升 | ✅（+50%） |
| 步数 | 1,200 |

## 结论

**导出的 SFT/DPO 制品可以被真实训练循环消费，且模型确实学到东西。**
数据飞轮的「导出 → 训练」这段从"结构可解析"推进到了"可学习"。

## 复现

```bash
# 生成真实制品
python -c "..."  # 用 ddp_data.llm_export.export_sft/export_dpo

# 跑探测（CLI 对未收敛会返回 1）
PYTHONPATH=data-platform .venv/bin/python scripts/train/run_training_probe.py \
  --sft /tmp/train-probe-data/sft_messages.jsonl \
  --dpo /tmp/train-probe-data/preference_dpo.jsonl \
  --out training-probe-report.json

# 单测
PYTHONPATH=data-platform .venv/bin/python -m pytest data-platform/tests/test_training_probe.py -q
```

## 9 项单测覆盖的不变量

1. SFT loss 真实下降（不收敛即数据不可用的信号）
2. **同种子两次运行结果完全一致**（可复现，与随机噪声的分界）
3. 种子确实生效（不同种子产生不同初始状态）
4. DPO 判对率提升
5. DPO 可复现
6. 空输入明确报错（不静默返回空结果）
7. 单条短文本可处理（context 自适应降级）
8. summarize 同时兼容结果对象与 dict 落盘形态
9. CLI 能解析真实导出制品（OpenAI messages / chosen-rejected）

## 诚实边界

1. **这是「数据可用性 + 可学习性」验证，不是生产级微调**：字符级 tokenizer、
   微型模型（8988 参数）、小样本（8 条文本 / 6 对偏好）
2. **环境无 torch/transformers**，用 numpy 手写前向/反向——实现经过验证，
   但不等于在真实训练框架下的表现
3. 生成样本是模型从训练数据学到的字符序列，**不是语义理解的证明**
4. 未验证：大模型规模下的显存/训练稳定性、真实模型架构的收敛性
5. 单测中的 SFT 探测用 25–30 epochs；生产训练的 epoch 数、学习率需单独调参
