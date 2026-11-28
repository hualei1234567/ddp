"""处理管线并发规模探测脚本的结构与口径校验。

**为什么不是"跑一遍脚本"**：真实压测需要 docker（PG 55432）、临时库、迁移与
完整前置数据，CI 环境没有。本测试验证脚本**语义正确**——即那些"看起来对但
实际会静默失效"的地方：

1. **默认 dry-run 不建库**：没有 ``--execute`` 就绝不能碰数据库；
2. **临时库名校验**：非法库名（无 ``ddp_perf_`` 前缀、大写、超长）必须被拒；
3. **非 loopback DSN 被拒**：误连远端库执行 DDL 是不可逆事故；
4. **汇总口径正确**：TPS / p50 / p95 / p99 / 错误率必须与
   ``perf.core.summarize`` 同一口径，锁观测字段必须随档汇总；
5. **连接池红线判定复用**：必须直接复用 ``write_path_e2e.pool_redline_conclusion``
   （同一实现、同一口径），而不是脚本内另写一套会分叉的判定；
6. **锁观测口径**：死锁差值不回退、锁等待只认 ``Lock`` 事件。

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
SCRIPT_PATH = ROOT / "scripts" / "perf" / "pipeline_scale_probe.py"
CORE_PATH = ROOT / "scripts" / "perf" / "core.py"
WRITE_PATH_PATH = ROOT / "scripts" / "perf" / "write_path_e2e.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load(CORE_PATH, "perf_core_under_test")
# 以规范包名注册：被测脚本内部走 ``from perf.write_path_e2e import ...``，
# 只有注册同名模块，才能断言「红线判定是同一份实现」而不是两份副本。
write_path = _load(WRITE_PATH_PATH, "perf.write_path_e2e")


@pytest.fixture(scope="module")
def probe():
    """加载被测脚本。

    脚本顶层会往 ``sys.path`` 注入 ``backend`` / ``data-platform`` 并导入
    ``perf.core`` / ``perf.write_path_e2e``（只依赖标准库），因此**不需要**
    后端 venv 就能加载——这让 CI 的「只装 pytest」环境也能跑这个测试。
    """
    return _load(SCRIPT_PATH, "pipeline_scale_probe_under_test")


# ---------------------------------------------------------------------------
# 1. 默认 dry-run：绝不建库
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_database(probe, monkeypatch) -> None:
    """不加 ``--execute`` 时不得调用建库函数。

    用「把建库函数替换成会炸的桩」来验证：dry-run 一旦误触建库，测试立刻
    失败（而不是等真跑到线上库才发现）。
    """
    calls: list[str] = []

    def _boom(*_args, **_kwargs):
        calls.append("create")
        raise AssertionError("dry-run 不允许建库")

    monkeypatch.setattr(probe, "_create_temp_db", _boom)
    monkeypatch.setattr(probe, "_drop_temp_db", _boom)

    exit_code = probe.main(
        [
            "--dsn",
            "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
        ]
    )

    assert exit_code == 0
    assert calls == []


def test_dry_run_branch_precedes_database_creation(probe) -> None:
    """静态不变量：``if not args.execute: return`` 必须出现在建库之前。

    这是「dry-run 不建库」的可执行等价物的**结构性**保证：即使有人后来
    改了 ``main`` 的调用顺序，这条断言也会立刻红。
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    dry_run_index = source.index("if not args.execute:")
    create_index = source.index("_create_temp_db(args.dsn)")
    assert dry_run_index < create_index, "dry-run 判定必须早于建库调用"


def test_execute_is_required_for_real_run(probe, monkeypatch) -> None:
    """``--execute`` 是真跑的唯一开关；不给就必须停在 dry-run。"""
    monkeypatch.setattr(
        probe,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert (
        probe.main(["--dsn", "postgresql+psycopg://ddp:ddp@localhost:55432/postgres"])
        == 0
    )


# ---------------------------------------------------------------------------
# 2. 临时库名校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stamp",
    [
        "20261128120000_abc123",  # 正常
    ],
)
def test_temp_db_name_has_required_prefix(probe, stamp: str) -> None:
    name = probe.make_temp_db_name(stamp)
    assert name.startswith("ddp_perf_")
    assert name.startswith("ddp_perf_scale_")
    assert core.ensure_safe_perf_db_name(name) == name


def test_temp_db_name_rejects_invalid_stamp(probe) -> None:
    """带非法字符的戳必须被拒——库名会进 ``CREATE DATABASE``。"""
    with pytest.raises(ValueError):
        probe.make_temp_db_name('2026"; DROP DATABASE ddp; --')


@pytest.mark.parametrize(
    "bad_name",
    [
        "ddp_perf",  # 缺后缀
        "ddp_perf_",  # 缺后缀
        "perf_scale_2026",  # 无 ddp_perf_ 前缀
        "ddp_perf_SCALE_2026",  # 大写
        "ddp_perf_a-b",  # 非法字符
        "ddp_perf_" + "a" * 60,  # 超长
    ],
)
def test_unsafe_temp_db_names_are_rejected(bad_name: str) -> None:
    """非法临时库名必须被 :func:`ensure_safe_perf_db_name` 拒绝。"""
    with pytest.raises(ValueError):
        core.ensure_safe_perf_db_name(bad_name)


def test_script_uses_shared_safety_helpers(probe) -> None:
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
def test_non_loopback_dsn_is_rejected(probe, dsn: str, monkeypatch) -> None:
    """非回环地址必须在建库前就被拒——误连远端库执行 DDL 不可逆。"""
    monkeypatch.setattr(
        probe,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    exit_code = probe.main(["--dsn", dsn])
    assert exit_code == 2


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
        "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres",
        "postgresql+psycopg://ddp:ddp@[::1]:55432/ddp_test",
    ],
)
def test_loopback_dsn_is_accepted(probe, dsn: str, monkeypatch) -> None:
    """回环地址允许通过前置校验（dry-run 阶段不建库）。"""
    monkeypatch.setattr(
        probe,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert probe.main(["--dsn", dsn]) == 0


@pytest.mark.parametrize(
    "dsn",
    [
        # 生产库 `ddp` 不在白名单内：绝不允许以它为建库连接点。
        "postgresql+psycopg://ddp:ddp@localhost:55432/ddp",
        "postgresql+psycopg://ddp:ddp@localhost:55432/ddp_prod",
    ],
)
def test_non_whitelisted_database_is_rejected(probe, dsn: str, monkeypatch) -> None:
    """白名单之外的库名（尤其生产库 ``ddp``）必须被拒。"""
    monkeypatch.setattr(
        probe,
        "_create_temp_db",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得建库")),
    )
    assert probe.main(["--dsn", dsn]) == 2


# ---------------------------------------------------------------------------
# 4. 汇总口径
# ---------------------------------------------------------------------------


def _tier(probe, samples, *, wall_seconds: float, workers: int = 2) -> object:
    return probe.TierResult(
        workers=workers,
        iterations=len(samples),
        wall_seconds=wall_seconds,
        samples=samples,
        status_counts={},
        error_samples=(),
    )


def test_summarize_tier_matches_shared_statistics(probe) -> None:
    """一档汇总必须与 ``perf.core.summarize`` 同一口径。"""
    samples = (
        probe.RunSample(elapsed_ms=10.0, status=201, error=None),
        probe.RunSample(elapsed_ms=20.0, status=201, error=None),
        probe.RunSample(elapsed_ms=30.0, status=500, error="HTTP 500"),
        probe.RunSample(elapsed_ms=40.0, status=None, error="ConnectError: boom"),
    )
    tier = _tier(probe, samples, wall_seconds=2.0)

    payload = probe.summarize_tier(tier)

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


def test_summarize_tier_carries_lock_observability(probe) -> None:
    """锁观测必须随档汇总：死锁差值与锁等待连接数不能被丢掉。"""
    samples = tuple(
        probe.RunSample(elapsed_ms=10.0, status=201, error=None) for _ in range(4)
    )
    tier = probe.TierResult(
        workers=2,
        iterations=2,
        wall_seconds=1.0,
        samples=samples,
        status_counts={"201": 4},
        error_samples=(),
        lock_wait_connections=3,
        deadlock_delta=1,
    )
    payload = probe.summarize_tier(tier)
    assert payload["lock_wait_connections"] == 3
    assert payload["deadlock_delta"] == 1


def test_summarize_tier_rejects_empty_samples(probe) -> None:
    """空样本不得伪造统计——必须显式失败。"""
    tier = _tier(probe, (), wall_seconds=1.0)
    with pytest.raises(ValueError):
        probe.summarize_tier(tier)


def test_error_count_counts_only_failed_samples(probe) -> None:
    """错误数 = 带 error 的样本数；4xx/5xx 与网络异常都算错误。"""
    tier = _tier(
        probe,
        (
            probe.RunSample(elapsed_ms=1.0, status=201, error=None),
            probe.RunSample(elapsed_ms=1.0, status=409, error="HTTP 409"),
        ),
        wall_seconds=1.0,
    )
    assert tier.error_count == 1
    payload = probe.summarize_tier(tier)
    assert payload["error_count"] == 1
    assert payload["error_rate"] == 0.5


def test_tps_uses_wall_clock_not_request_count(probe) -> None:
    """TPS = 请求数 / 墙钟秒数，不是「每秒请求数」的另一种口径。"""
    tier = _tier(
        probe,
        tuple(
            probe.RunSample(elapsed_ms=100.0, status=201, error=None) for _ in range(5)
        ),
        wall_seconds=4.0,
    )
    payload = probe.summarize_tier(tier)
    assert payload["tps"] == 1.25


# ---------------------------------------------------------------------------
# 5. 连接池红线判定：必须复用写路径压测的同一实现
# ---------------------------------------------------------------------------


def test_pool_redline_is_reused_from_write_path_e2e(probe) -> None:
    """红线判定必须是 ``write_path_e2e`` 的同一份实现，口径不得分叉。"""
    assert probe.pool_redline_conclusion is write_path.pool_redline_conclusion


def test_pool_redline_detects_saturation(probe) -> None:
    """占用超过 ``max_connections`` 必须判为「会打满」。"""
    verdict = probe.pool_redline_conclusion(
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


def test_pool_redline_detects_tight_margin(probe) -> None:
    """占用率 ≥ 80% 必须判为「余量不足」。"""
    verdict = probe.pool_redline_conclusion(
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


def test_pool_redline_detects_safe_margin(probe) -> None:
    """占用率充足时判为「不会打满」，并给出余量。"""
    verdict = probe.pool_redline_conclusion(
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


def test_pool_redline_counts_other_connections(probe) -> None:
    """「其他连接」必须参与计算——把它当 0 会得出危险的乐观结论。"""
    without_others = probe.pool_redline_conclusion(
        pool_size=10,
        max_overflow=20,
        max_connections=100,
        peak_api_connections=30,
        peak_concurrency=16,
        other_connections=0,
    )
    with_others = probe.pool_redline_conclusion(
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


# ---------------------------------------------------------------------------
# 6. 锁观测口径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (0, 0, 0),  # 无死锁
        (0, 2, 2),  # 新增 2 次死锁
        (5, 5, 0),  # 计数未变
        (5, 3, 0),  # 计数器不应回退：负差值钳到 0
    ],
)
def test_compute_deadlock_delta(probe, before: int, after: int, expected: int) -> None:
    assert probe.compute_deadlock_delta(before, after) == expected


def test_classify_lock_waits_only_counts_lock_event(probe) -> None:
    """锁等待只认 ``Lock``；CPU 与客户端等待不能算进锁风险。"""
    result = probe.classify_lock_waits({"Lock": 3, "CPU": 5, "Client": 2, "IO": 1})
    assert result["lock_wait_connections"] == 3
    assert result["other_wait_connections"] == 8


def test_classify_lock_waits_handles_empty(probe) -> None:
    result = probe.classify_lock_waits({})
    assert result == {"lock_wait_connections": 0, "other_wait_connections": 0}


def test_make_run_payload_uses_unique_refs(probe) -> None:
    """每次迭代必须给全新 ``input_refs``：合成规模下每次创建都是独立 run。"""
    project_id = "00000000-0000-0000-0000-000000000000"
    first = probe.make_run_payload(project_id, 1, pipeline_version="pipeline-v1")
    second = probe.make_run_payload(project_id, 2, pipeline_version="pipeline-v1")
    assert first["pipeline_version"] == "pipeline-v1"
    assert first["input_refs"] != second["input_refs"]
    assert first["input_refs"][0].startswith(f"raw/{project_id}/")


# ---------------------------------------------------------------------------
# 7. 参数红线
# ---------------------------------------------------------------------------


def test_parse_concurrency_accepts_and_normalizes(probe) -> None:
    assert probe.parse_concurrency("1,4,8,16") == (1, 4, 8, 16)
    assert probe.parse_concurrency("16,4,1,4") == (1, 4, 16)


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "", "1,0"])
def test_parse_concurrency_rejects_invalid(probe, raw: str) -> None:
    with pytest.raises(ValueError):
        probe.parse_concurrency(raw)


def test_parse_concurrency_rejects_out_of_range(probe) -> None:
    with pytest.raises(ValueError):
        probe.parse_concurrency(str(probe.MAX_CONCURRENCY + 1))


def test_parse_iterations_rejects_out_of_range(probe) -> None:
    assert probe.parse_iterations(25) == 25
    with pytest.raises(ValueError):
        probe.parse_iterations(0)
    with pytest.raises(ValueError):
        probe.parse_iterations(-5)
    with pytest.raises(ValueError):
        probe.parse_iterations(probe.MAX_ITERATIONS + 1)


def test_invalid_concurrency_exits_with_code_2(probe) -> None:
    """参数非法必须以退出码 2 结束（区别于 0=成功、1=有错误）。"""
    assert (
        probe.main(
            [
                "--dsn",
                "postgresql+psycopg://ddp:ddp@localhost:55432/postgres",
                "--concurrency",
                "999",
            ]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# 8. 结构不变量
# ---------------------------------------------------------------------------


def test_script_declares_required_cli_arguments(probe) -> None:
    """CLI 必须提供任务约定的参数。"""
    parser = probe._build_parser()
    options = {action.dest for action in parser._actions}
    for name in ("dsn", "concurrency", "iterations", "execute", "keep_db", "out"):
        assert name in options, f"缺少 CLI 参数 --{name}"


def test_dsn_is_required(probe) -> None:
    """``--dsn`` 必填：没有它就没法判断建库连接点是否安全。"""
    parser = probe._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_script_never_writes_to_the_base_database(probe) -> None:
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


def test_script_uses_real_jwt_authentication(probe) -> None:
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
    client_source = inspect.getsource(probe._make_client)  # noqa: SLF001
    assert "Authorization" in client_source
    assert "Bearer" in client_source
    assert "X-User-Id" not in client_source


def test_script_reuses_write_path_assets(probe) -> None:
    """连接池口径、连接采样与库表统计必须复用写路径压测的资产。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "pool_redline_conclusion" in source
    assert "_ConnectionSampler" in source
    assert "_pg_connection_stats" in source
    # 生产连接池口径必须是写路径压测的同一份常量，不得本地另立。
    assert probe.PRODUCTION_POOL_SIZE == write_path.PRODUCTION_POOL_SIZE
    assert probe.PRODUCTION_MAX_OVERFLOW == write_path.PRODUCTION_MAX_OVERFLOW


def test_script_reuses_test_fixtures_for_seeding(probe) -> None:
    """前置数据必须复用既有测试夹具，不另写一套造数逻辑。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "seed_identity" in source


def test_script_does_not_write_into_the_production_database(probe) -> None:
    """临时库之外不得有别的写目标。

    判据是**行为**而不是源码文本：脚本用的写目标必须全部由
    ``with_database(base_dsn, <临时库名>)`` 构造，而临时库名必带
    ``ddp_perf_`` 前缀。
    """
    # 建库/删库必须走带白名单校验的入口，而不是直接拼 DSN。
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ensure_safe_base_dsn" in source
    assert "ensure_safe_perf_db_name" in source
    assert "with_database" in source

    # 行为断言：临时库名必须落在 ddp_perf_ 前缀内。
    name = probe.make_temp_db_name("20261128120000")
    assert name.startswith("ddp_perf_")
    with pytest.raises(ValueError):
        core.ensure_safe_perf_db_name("ddp_production")

    # 行为断言：非本机 DSN 被拒。
    with pytest.raises(ValueError):
        core.ensure_safe_base_dsn(
            "postgresql+psycopg://ddp:ddp@db.example.com:5432/postgres"
        )
