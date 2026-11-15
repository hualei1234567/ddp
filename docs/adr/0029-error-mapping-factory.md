# ADR-0029 错误映射收敛为单一工厂（R-11）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：审计 R-11、R-12（ADR-0028）；`CLAUDE.md` 第五节（禁止复制业务规则）
- 影响范围：`app/api/errors.py`；对外错误体形状不变

## 1. 背景

`app/api/errors.py` 里 9 个域各有一套映射：

```
_<DOMAIN>_STATUS_BY_CODE    # code → HTTP 状态
_<DOMAIN>_MESSAGES          # code → 固定文案
def <domain>_error_to_app() # 查表 + 兜底 + 构造 AppError
```

9 个函数体逐字近似，只有两处真实差异：

| 差异 | 涉及域 | 处理 |
|---|---|---|
| 兜底文案不同 | 全部 | 提取为 `<DOMAIN>_FALLBACK` 常量 |
| 是否透传 `details` | 仅 ingestion / episode | 做成 `with_details` 开关 |

审计记为 R-11（P2：巨型文件 + 重复）。

## 2. 决策

**映射逻辑收敛为一个 `_mapped()` 工厂；各域的表保持分开。**

```python
def dataset_error_to_app(exc: DatasetDomainError) -> AppError:
    return _mapped(
        exc,
        status_by_code=_DATASET_STATUS_BY_CODE,
        messages=_DATASET_MESSAGES,
        fallback=_DATASET_FALLBACK,
    )
```

### 2.1 为什么不把 9 张表也合并

看起来合并成一张大表能消掉更多行，但代价是把**域边界**合掉：

- 各域的 code 是独立的评审单元（新增 code 要过自己的契约评审）；
- 合并后"某个域新增 code"变成"改全局共享表"，一个域的改动会波及所有域的评审面；
- 表内容上的重叠（如 `resource_not_found` 都是 404）是巧合，不是同一语义。

所以本次只收敛**逻辑**，不收敛**数据**。这与 R-8 的处理原则一致：
把重复的判断收成一份，把真实的差异显式化。

### 2.2 `details` 透传是刻意保留的差异

ingestion / episode 两个域的 `details` 带的是容量上限、期望值与实际值这类
**对客户端排障有用且不敏感**的信息。其余域不透传，避免无差别暴露内部字段。

`test_details_are_propagated_where_declared` 用真实的 `FileTooLargeError`
验证这条路径——用自定义异常测不到，因为 `details` 是父类 `__init__` 设置的。

### 2.3 与 R-12 的衔接

R-12（ADR-0028）修掉了 modelops / supplier 用 `str(exc)` 回显的问题。
收敛到工厂后，"不回显 `str(exc)`" 变成**结构性保证**：
工厂里根本没有读取 `str(exc)` 的代码路径，新增域也不会再写出来。

## 3. 关于文件行数

收敛后 `errors.py` 仍是 673 行（收敛前 644 行）——**行数没有下降**，
因为映射逻辑本来就很短，占大头的是 9 张表本身。

这说明 R-11 的"575 行巨型文件"诊断需要修正：**它不是逻辑膨胀，而是表数据**。
把它按域拆成 9 个文件能降低单文件行数，但会让"改一个错误码文案"
从"改一个文件"变成"找一个文件 + 改 + 确认导入没断"，收益可疑。

因此本次**不做文件拆分**，只消除重复逻辑。这属于对审计结论的修正，
不是回避：`CLAUDE.md` 的"500 行触发评审"是触发评审，不是强制拆分，
评审结论是"内容性质是常量表，拆分无收益"。

## 4. 验收

| 项 | 结果 |
|---|---|
| 映射函数走同一工厂 | 测试用 `inspect.getsource` 断言每个函数含 `_mapped(` 且无判定逻辑 |
| 已知 code 文案仍具体 | `test_known_codes_keep_their_specific_message` |
| 敏感串不外泄 | `test_modelops_errors_use_fixed_messages` 等 4 条 |
| details 透传 | `test_details_are_propagated_where_declared`（真实 `FileTooLargeError`） |
| 真实 PostgreSQL 全量 | 见交付汇报 |
| `make check` | 通过 |
