# ADR-0016 JWT 校验改用 PyJWT

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-14
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0001；`CLAUDE.md`《复用优先与代码质量标准》第一/二节；审计报告 R-6
- 影响范围：`backend/app/infrastructure/auth/jwt.py` 内部实现；对外行为不变

## 1. 背景

阶段 0 的 JWT 校验是手写实现（189 行，仅用 `hmac` + `base64` + `json`），
理由是"避免为一次 HS256 校验引入第三方依赖"。

审计（R-6）指出这与 `CLAUDE.md` 第二节冲突：

> 不得手写已知有标准解法的底层协议（如 multipart 断点续传、**HS256 校验**、
> S3 签名、日期时区换算）

手写实现的承接风险：算法混淆、`crit` 扩展、Base64URL 填充变体、非对象声明
等边界都要自己覆盖；每新增一种异常形态都要改一次安全代码。

## 2. 决策

**改用 PyJWT 做签名与 JSON 解析，claim 策略仍由 DDP 自己裁决。**

关键做法是 `_DECODE_OPTIONS` 关闭 PyJWT 的全部 claim 校验
（`verify_exp` / `verify_nbf` / `verify_iat` / `verify_iss` / `verify_aud` /
`verify_sub`），只保留签名校验，然后由本模块的 `_validate_*` 执行 DDP 的严格语义。

理由：PyJWT 的 claim 语义比 DDP 宽松（例如不拒绝 `exp: true` 或 `exp: NaN`）。
若让库同时管签名与 claim，就会出现两套判定，且降级行为由库版本决定。
把"密码学"交给库、把"业务策略"留给自己，职责边界清晰且可测。

保留的 DDP 自有校验（33 条用例锁定）：

| 规则 | 原因 |
|---|---|
| 只接受 `HS256` | 防算法混淆；`none` 与 HS512 一律拒绝 |
| 拒绝 `crit` 扩展 | 表示校验方必须理解该扩展，未实现时必须拒绝 |
| `exp`/`nbf` 必须是有限数值，排除 `bool`/`NaN`/`Infinity` | Python 中 `True == 1`、`NaN` 比较恒假，会绕过时间窗口 |
| `aud` 支持字符串或字符串数组 | 兼容多受众签发方 |
| 令牌长度上限 8192、`sub` 上限 512、必须 ASCII | 防超长/畸形输入消耗资源 |
| 失败只抛 `TokenValidationError`，不区分原因 | 避免向攻击者泄漏校验细节 |

## 3. 依赖评估（`CLAUDE.md` §三）

| 项 | 结论 |
|---|---|
| 用途 | HS256 签名校验与结构化解析 |
| 替代方案 | 手写（现方案，违反第二节）；`python-jose`（依赖 `ecdsa`/`rsa`，体积大，维护活跃度低）；`authlib`（功能远超需求） |
| 版本固定 | `PyJWT==2.10.1`（`requirements.txt` 精确固定） |
| 体积/性能 | 纯 Python，无 C 扩展，无额外传递依赖（仅 `typing_extensions` 视版本） |
| 许可证 | MIT |
| 安全公告 | 2.10.1 为当前维护线；历史 CVE（CVE-2022-29217 算法混淆、CVE-2024-33663/33664）均已在 2.x 修复，本方案另外显式限定 `algorithms=["HS256"]` |

## 4. 影响与回滚

- 对外行为不变：`decode_hs256_token` 签名、`JwtClaims`、`TokenValidationError`
  全部保持；调用方无需改动。
- 测试：原有 21 条 + 新增 4 条负向探针（`crit`、超长、非 ASCII、负容差）
  + 8 条参数化畸形令牌 = 33 passed。
- 回滚：还原 `jwt.py` 与 `requirements.txt` 两处即可，无数据迁移、无配置变化。

## 5. 未做的事

- 不引入令牌签发（本系统令牌由外部身份源签发，DDP 只校验）。
- 不支持 RS256/JWKS。若将来接入外部 IdP 的 JWKS，需另立 ADR 并引入
  `PyJWKClient`，届时不得退回手写解析。
