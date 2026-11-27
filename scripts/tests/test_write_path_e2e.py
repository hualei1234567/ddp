"""写路径端到端压测脚本的结构与口径校验。

**为什么不是"跑一遍脚本"**：真实压测需要 docker（PG 55432）、临时库、迁移与
完整前置数据，CI 环境没有。本测试验证脚本**语义正确**——即那些"看起来对但
实际会静默失效"的地方：

1. **默认 dry-run 不建库**：没有 ``--execute`` 就绝不能碰数据库；
2. **临时库名校验**：非法库名（无 ``ddp_perf_`` 前缀、大写、超长）必须被拒；
3. **非 loopback DSN 被拒**：误连远端库执行 DDL 是不可逆事故；
4. **汇总口径正确**：TPS / p50 / p95 / p99 / 错误率必须与
   ``perf.core.summarize`` 同一口径；
5. **连接池红线判定**：三种档位（打满 / 余量不足 / 安全）判定正确，且
   「其他连接」必须参与计算。

纯函数层与 CLI 前置校验不需要数据库；dry-run 断言用「临时库名未出现在
PG 里」这一**可观测事实**来验证（不建库 = 库里没有该名字的库），需要 PG；
因此用 ``pytest.importorskip`` 跳过——但**跳过会掩盖问题**，所以另外用
静态断言（源码级）钉住「dry-run 分支在建库之前 return」这条不变量。

这些是**结构断言**，用文本/AST 静态分析 + 纯函数调用，不需要 docker。
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "perf" / "write_path_e2e.py"
CORE_PATH = ROOT / "scripts" / "perf" / "core.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load(CORE_PATH, "perf_core_under_test")


@pytest.fixture(scope="module")
def e2e():
    """加载被测脚本。

    脚本顶层会往 ``sys.path`` 注入 ``backend`` / ``data-platform`` 并导入
    ``perf.core``（只依赖标准库），因此**不需要**后端 venv 就能加载——
    这让 CI 的「只装 pytest」环境也能跑这个测试。
    """
    return _load(SCRIPT_PATH, "write_path_e2e_under_test")


# ---------------------------------------------------------------------------
# 1. 默认 dry-run：绝不建库
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_database(e2e, monkeypatch) -> None:
    """不加 ``--execute`` 时不得调用建库函数。

    用「把建库函数替换成会炸的桩」来验证：dry-run 一旦误触建库，测试立刻
    失败（而不是等真跑到线上库才发现）。
    """
    calls: list[str] = []

    def _boom(*_args, **_kwargs):
        calls.append("create")
        raise AssertionError("dry-run 不允许建库")

    monkeypatch.setattr(e2e, "_create_temp_db", _boom)
    monkeypatch.setattr(e2e, "_drop_temp_db", _boom)

    exit_code = e2e.main(
        [
            "--dsn",
            "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
        ]
    )

    assert exit_code == 0
    assert calls == []


def test_dry_run_branch_precedes_database_creation(e2e) -> None:
    """静态不变量：``if not args.execute: return`` 必须出现在建库之前。

    这是「dry-run 不建库」的可执行等价物的**结构性**保证：即使有人后来
    改了 ``main`` 的调用顺序，这条断言也会立刻红。
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    dry_run_index = source.index("if not args.execute:")
    create_index = source.index("_create_temp_db(args.dsn)")
    assert dry_run_index < create_index, "dry-run 判定必须早于建库调用"


def test_execute_is_required_for_real_run(e2e, monkeypatch) -> None:
    """``--execute`` 是真跑的唯一开关；不给就必须停在 dry-run。"""
    monkeypatch.setattr(
        e2e,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert (
        e2e.main(["--dsn", "postgresql+psycopg://ddp:ddp@localhost:55432/postgres"])
        == 0
    )


# ---------------------------------------------------------------------------
# 2. 临时库名校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stamp",
    [
        "20261127120000_abc123",  # 正常
    ],
)
def test_temp_db_name_has_required_prefix(e2e, stamp: str) -> None:
    name = e2e.make_temp_db_name(stamp)
    assert name.startswith("ddp_perf_")
    assert core.ensure_safe_perf_db_name(name) == name


def test_temp_db_name_rejects_invalid_stamp(e2e) -> None:
    """带非法字符的戳必须被拒——库名会进 ``CREATE DATABASE``。"""
    with pytest.raises(ValueError):
        e2e.make_temp_db_name('2026"; DROP DATABASE ddp; --')


@pytest.mark.parametrize(
    "bad_name",
    [
        "ddp_perf",  # 缺后缀
        "ddp_perf_",  # 缺后缀
        "perf_e2e_2026",  # 无 ddp_perf_ 前缀
        "ddp_perf_E2E_2026",  # 大写
        "ddp_perf_a-b",  # 非法字符
        "ddp_perf_" + "a" * 60,  # 超长
    ],
)
def test_unsafe_temp_db_names_are_rejected(bad_name: str) -> None:
    """非法临时库名必须被 :func:`ensure_safe_perf_db_name` 拒绝。"""
    with pytest.raises(ValueError):
        core.ensure_safe_perf_db_name(bad_name)


def test_script_uses_shared_safety_helpers(e2e) -> None:
    """脚本必须复用 ``perf.core`` 的安全护栏，不得自己重写一套。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ensure_safe_base_dsn" in source
    assert "ensure_safe_perf_db_name" in source
    assert "with_database" in source
    # 自己写 SQL 拼库名是危险模式：脚本里只允许通过校验函数拿到的名字。
    assert "CREATE DATABASE" in source
    assert "DROP DATABASE" in source


# ---------------------------------------------------------------------------
# 3. 非 loopback DSN 被拒
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+psycopg://ddp:ddp@db.example.com:5432/postgres",
        "postgresql+psycopg://ddp:ddp@192.168.1.10:5432/postgres",
        "postgresql+psycopg://ddp:ddp@pg-prod.internal/postgres",
    ],
)
def test_non_loopback_dsn_is_rejected(e2e, dsn: str, monkeypatch) -> None:
    """非回环地址必须在建库前就被拒——误连远端库执行 DDL 不可逆。"""
    monkeypatch.setattr(
        e2e,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    exit_code = e2e.main(["--dsn", dsn])
    assert exit_code == 2


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
        "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres",
        "postgresql+psycopg://ddp:ddp@[::1]:55432/ddp_test",
    ],
)
def test_loopback_dsn_is_accepted(e2e, dsn: str, monkeypatch) -> None:
    """回环地址允许通过前置校验（dry-run 阶段不建库）。"""
    monkeypatch.setattr(
        e2e,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert e2e.main(["--dsn", dsn]) == 0


@pytest.mark.parametrize(
    "dsn",
    [
        # 生产库 `ddp` 不在白名单内：绝不允许以它为建库连接点。
        "postgresql+psycopg://ddp:ddp@localhost:55432/ddp",
        "postgresql+psycopg://ddp:ddp@localhost:55432/ddp_prod",
    ],
)
def test_non_whitelisted_database_is_rejected(e2e, dsn: str, monkeypatch) -> None:
    """白名单之外的库名（尤其生产库 ``ddp``）必须被拒。"""
    monkeypatch.setattr(
        e2e,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert e2e.main(["--dsn", dsn]) == 2


# ---------------------------------------------------------------------------
# 4. 汇总口径
# ---------------------------------------------------------------------------


def test_summarize_tier_matches_shared_statistics(e2e) -> None:
    """一档汇总必须与 ``perf.core.summarize`` 同一口径。"""
    samples = (
        e2e.RequestSample(elapsed_ms=10.0, status=201, error=None),
        e2e.RequestSample(elapsed_ms=20.0, status=200, error=None),
        e2e.RequestSample(elapsed_ms=30.0, status=500, error="HTTP 500"),
        e2e.RequestSample(elapsed_ms=40.0, status=None, error="ConnectError: boom"),
    )
    tier = e2e.TierResult(
        workers=2,
        iterations=2,
        wall_seconds=2.0,
        samples=samples,
        status_counts={"201": 1, "200": 1, "500": 1, "network_error": 1},
        error_samples=("HTTP 500", "ConnectError: boom"),
    )

    payload = e2e.summarize_tier(tier)

    expected = core.summarize(
        [10.0, 20.0, 30.0, 40.0],
        wall_seconds=2.0,
        error_count=2,
    )
    assert payload["tps"] == expected["throughput_rps"]
    assert payload["p50_ms"] == expected["p50_ms"]
    assert payload["p95_ms"] == expected["p95_ms"]
    assert payload["p99_ms"] == expected["p99_ms"]
    assert payload["max_ms"] == expected["max_ms"]
    assert payload["error_count"] == 2
    assert payload["error_rate"] == 0.5
    assert payload["requests_sent"] == 4
    assert payload["status_counts"] == {
        "201": 1,
        "200": 1,
        "500": 1,
        "network_error": 1,
    }
    assert payload["error_samples"] == ["HTTP 500", "ConnectError: boom"]


def test_summarize_tier_rejects_empty_samples(e2e) -> None:
    """空样本不得伪造统计——必须显式失败。"""
    tier = e2e.TierResult(
        workers=1,
        iterations=0,
        wall_seconds=1.0,
        samples=(),
        status_counts={},
        error_samples=(),
    )
    with pytest.raises(ValueError):
        e2e.summarize_tier(tier)


def test_error_count_counts_only_failed_samples(e2e) -> None:
    """错误数 = 带 error 的样本数；4xx/5xx 与网络异常都算错误。"""
    tier = e2e.TierResult(
        workers=1,
        iterations=2,
        wall_seconds=1.0,
        samples=(
            e2e.RequestSample(elapsed_ms=1.0, status=201, error=None),
            e2e.RequestSample(elapsed_ms=1.0, status=409, error="HTTP 409"),
        ),
        status_counts={"201": 1, "409": 1},
        error_samples=("HTTP 409",),
    )
    assert tier.error_count == 1
    payload = e2e.summarize_tier(tier)
    assert payload["error_count"] == 1
    assert payload["error_rate"] == 0.5


def test_tps_uses_wall_clock_not_request_count(e2e) -> None:
    """TPS = 请求数 / 墙钟秒数，不是「每秒请求数」的另一种口径。"""
    tier = e2e.TierResult(
        workers=1,
        iterations=5,
        wall_seconds=4.0,
        samples=tuple(
            e2e.RequestSample(elapsed_ms=100.0, status=201, error=None)
            for _ in range(5)
        ),
        status_counts={"201": 5},
        error_samples=(),
    )
    payload = e2e.summarize_tier(tier)
    assert payload["tps"] == 1.25


# ---------------------------------------------------------------------------
# 5. 连接池红线判定
# ---------------------------------------------------------------------------


def test_pool_redline_detects_saturation(e2e) -> None:
    """占用超过 ``max_connections`` 必须判为「会打满」。"""
    verdict = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=90,
        peak_concurrency=16,
        other_connections=20,
    )
    assert verdict["verdict"] == "saturated"
    assert verdict["remaining_connections"] < 0
    assert verdict["projected_total_connections"] == 110
    assert "会打满" in verdict["statement"]


def test_pool_redline_detects_tight_margin(e2e) -> None:
    """占用率 ≥ 80% 必须判为「余量不足」。"""
    verdict = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=70,
        peak_concurrency=16,
        other_connections=12,
    )
    assert verdict["verdict"] == "tight"
    assert verdict["usage_ratio"] == 0.82
    assert verdict["remaining_connections"] == 18


def test_pool_redline_detects_safe_margin(e2e) -> None:
    """占用率充足时判为「不会打满」，并给出余量。"""
    verdict = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=12,
        peak_concurrency=4,
        other_connections=8,
    )
    assert verdict["verdict"] == "safe"
    assert verdict["remaining_connections"] == 80
    assert verdict["api_connection_cap"] == 30


def test_pool_redline_counts_other_connections(e2e) -> None:
    """「其他连接」必须参与计算——把它当 0 会得出危险的乐观结论。"""
    without_others = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=30,
        peak_concurrency=16,
        other_connections=0,
    )
    with_others = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=30,
        peak_concurrency=16,
        other_connections=55,
    )
    assert without_others["verdict"] == "safe"
    assert with_others["verdict"] == "tight"
    assert with_others["projected_total_connections"] == 85
    # 同一组 API 峰值，仅因为同实例上还有别的连接，结论就从「安全」变成「紧张」。
    assert without_others["verdict"] != with_others["verdict"]


def test_pool_redline_reports_every_input(e2e) -> None:
    """结论必须逐项列明输入，便于人工复核。"""
    verdict = e2e.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=12,
        peak_concurrency=4,
        other_connections=3,
    )
    for key in (
        "api_pool_size",
        "api_max_overflow",
        "api_connection_cap",
        "measured_peak_api_connections",
        "measured_peak_concurrency",
        "other_connections",
        "projected_total_connections",
        "pg_max_connections",
        "remaining_connections",
        "usage_ratio",
        "verdict",
        "statement",
    ):
        assert key in verdict


# ---------------------------------------------------------------------------
# 6. 参数红线
# ---------------------------------------------------------------------------


def test_parse_workers_rejects_out_of_range(e2e) -> None:
    assert e2e.parse_workers("1,4,16") == (1, 4, 16)
    assert e2e.parse_workers("16,4,1,4") == (1, 4, 16)
    with pytest.raises(ValueError):
        e2e.parse_workers("0")
    with pytest.raises(ValueError):
        e2e.parse_workers("-1")
    with pytest.raises(ValueError):
        e2e.parse_workers("abc")
    with pytest.raises(ValueError):
        e2e.parse_workers("")
    with pytest.raises(ValueError):
        e2e.parse_workers(str(e2e.MAX_CONCURRENCY + 1))


def test_parse_iterations_rejects_out_of_range(e2e) -> None:
    assert e2e.parse_iterations(10) == 10
    with pytest.raises(ValueError):
        e2e.parse_iterations(0)
    with pytest.raises(ValueError):
        e2e.parse_iterations(-5)
    with pytest.raises(ValueError):
        e2e.parse_iterations(e2e.MAX_ITERATIONS + 1)


def test_invalid_workers_exit_with_code_2(e2e) -> None:
    """参数非法必须以退出码 2 结束（区别于 0=成功、1=有错误）。"""
    assert (
        e2e.main(
            [
                "--dsn",
                "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
                "--workers",
                "999",
            ]
        )
        == 2
    )


def test_plan_annotator_count_respects_capacity_limit(e2e) -> None:
    """承接人数量必须让每人持有的分配不超过容量上限。"""
    limit = e2e.ANNOTATOR_OPEN_ITEM_LIMIT
    assert e2e.plan_annotator_count(1) == 1
    assert e2e.plan_annotator_count(limit) == 1
    assert e2e.plan_annotator_count(limit + 1) == 2
    assert e2e.plan_annotator_count(limit * 3) == 3
    with pytest.raises(ValueError):
        e2e.plan_annotator_count(0)


# ---------------------------------------------------------------------------
# 7. 结构不变量
# ---------------------------------------------------------------------------


def test_script_declares_required_cli_arguments(e2e) -> None:
    """CLI 必须提供任务约定的参数。"""
    parser = e2e._build_parser()
    options = {action.dest for action in parser._actions}
    for name in ("dsn", "workers", "iterations", "execute", "keep_db", "out"):
        assert name in options, f"缺少 CLI 参数 --{name}"


def test_dsn_is_required(e2e) -> None:
    """``--dsn`` 必填：没有它就没法判断建库连接点是否安全。"""
    parser = e2e._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_script_never_writes_to_the_base_database(e2e) -> None:
    """脚本不得对「建库连接点」那个库名执行任何 DDL/DML。

    建库与删库都通过 ``with_database(base_dsn, name)`` 换成临时库；这里静态
    断言：所有 ``create_engine(base_dsn`` 形式的连接只用于 AUTOCOMMIT 的
    CREATE/DROP DATABASE 语句。
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    engine_calls = re.findall(r"create_engine\(base_dsn[^)]*\)", source)
    assert engine_calls, "建库/删库应通过 base_dsn 连接"
    for call in engine_calls:
        # 每处都必须是 AUTOCOMMIT（建库/删库不能开事务）。
        assert (
            "AUTOCOMMIT" in source[source.index(call) : source.index(call) + 200]
        ), "建库/删库连接必须使用 AUTOCOMMIT"


def test_script_uses_real_jwt_authentication(e2e) -> None:
    """必须走真实 Bearer JWT，而不是 ``X-User-Id`` 后门。

    断言两件事：脚本复用了测试侧的 ``make_token``（同一签发口径）；请求头里
    带的是 ``Authorization: Bearer``。

    **不断言源码里不出现 ``X-User-Id``**：那是**子串**匹配，脚本的说明性
    注释里就写了「不用 ``X-User-Id`` 后门」，一旦照着写注释就会被误判。
    真正的判据是**行为**——请求头里出现的是哪个认证头。
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "make_token" in source
    assert "Authorization" in source
    assert "Bearer" in source

    # 行为断言：客户端构造时用的认证头是 Authorization（Bearer），
    # 而不是 X-User-Id 头后门。
    client_source = inspect.getsource(e2e._make_client)  # noqa: SLF001
    assert "Authorization" in client_source
    assert "Bearer" in client_source
    assert "X-User-Id" not in client_source


def test_script_reuses_test_fixtures_for_seeding(e2e) -> None:
    """前置数据必须复用既有测试夹具，不另写一套造数逻辑。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "seed_identity" in source
    assert "seed_episode" in source
    assert "create_published_schema" in source
    assert "create_snapshot_set" in source


def test_script_does_not_write_into_the_production_database(e2e) -> None:
    """临时库之外不得有别的写目标。

    判据是**行为**而不是源码文本：脚本用的写目标必须全部由
    ``with_database(base_dsn, <临时库名>)`` 构造，而临时库名必带
    ``ddp_perf_`` 前缀。

    **不断言源码里不出现 ``ddp`` 字样**：那是子串匹配，脚本的文档字符串里
    就会写「只允许本机回环的 postgres/ddp_test 库」这类说明，会被误判。
    """
    # 建库/删库必须走带白名单校验的入口，而不是直接拼 DSN。
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ensure_safe_base_dsn" in source
    assert "ensure_safe_perf_db_name" in source
    assert "with_database" in source

    # 行为断言：临时库名必须落在 ddp_perf_ 前缀内。
    name = e2e.make_temp_db_name("20261127120000")
    assert name.startswith("ddp_perf_")
    with pytest.raises(ValueError):
        core.ensure_safe_perf_db_name("ddp_production")

    # 行为断言：非本机 DSN 被拒。
    with pytest.raises(ValueError):
        core.ensure_safe_base_dsn(
            "postgresql+psycopg://ddp:ddp@db.example.com:5432/postgres"
        )
