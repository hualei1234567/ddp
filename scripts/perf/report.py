"""基线报告元数据：硬件、数据库版本、Git 事实与 JSON 落盘。"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def memory_bytes() -> int | None:
    """读取物理内存；macOS 用 sysctl，Linux 用 /proc/meminfo。"""
    try:
        if sys.platform == "darwin":
            output = subprocess.run(  # noqa: S603 - 固定命令与参数
                ["sysctl", "-n", "hw.memsize"],  # noqa: S607 - 系统工具绝对名由 PATH 解析
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if output.returncode == 0:
                return int(output.stdout.strip())
        else:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def load_average() -> dict[str, float] | None:
    """1/5/15 分钟系统负载；基线与噪声判定都要它。"""
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return None
    return {"1m": round(one, 3), "5m": round(five, 3), "15m": round(fifteen, 3)}


def hardware_info() -> dict[str, Any]:
    total = memory_bytes()
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "memory_gib": round(total / 1024**3, 2) if total else None,
        "python": platform.python_version(),
    }


def git_info(repo_root: Path) -> dict[str, Any]:
    def _run(*args: str) -> str | None:
        try:
            completed = subprocess.run(  # noqa: S603 - 固定命令，参数为常量
                ["git", *args],  # noqa: S607 - 开发机 git 由 PATH 解析
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    return {
        "commit": _run("rev-parse", "HEAD"),
        "branch": _run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_run("status", "--porcelain")),
    }


def pg_version(dsn: str) -> str | None:
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return str(connection.execute(text("SHOW server_version")).scalar_one())
    except Exception:  # 报告元数据失败不应掩盖基线主体结果
        return None
    finally:
        engine.dispose()


def docker_image(name: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - 固定命令，name 为常量
            ["docker", "inspect", name, "--format", "{{.Config.Image}}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== 性能基线摘要 ===")
    hardware = report.get("environment", {}).get("hardware", {})
    print(
        f"硬件：{hardware.get('machine')} / CPU {hardware.get('cpu_count')} 核 / "
        f"内存 {hardware.get('memory_gib')} GiB / Python {hardware.get('python')}"
    )
    print(f"PostgreSQL：{report.get('environment', {}).get('pg_version')}")
    load = report.get("environment", {}).get("load_average_start")
    if load:
        print(f"运行开始负载：1m={load['1m']} 5m={load['5m']} 15m={load['15m']}")
    for run in report.get("runs", []):
        print(
            f"\n[规模 {run['scale']}] 迁移 {run.get('migration_seconds', 0):.2f}s，"
            f"造数 {run.get('seed_seconds', 0):.2f}s，行数 {run.get('row_counts')}"
        )
        if run.get("error"):
            print(f"  失败：{run['error']}")
        for key, scenario in run.get("scenarios", {}).items():
            summary = scenario["summary"]
            print(
                f"  {key:<42} n={summary['count']:<4} "
                f"p50={summary['p50_ms']:>8.2f}ms p95={summary['p95_ms']:>8.2f}ms "
                f"rps={summary['throughput_rps']:>8.2f} err={summary['error_count']}"
            )
        video = run.get("video", {})
        if video.get("status") not in {"OK", "SKIPPED"}:
            print(f"  video: {video.get('status')} {video.get('reason', '')}")
    thresholds = report.get("thresholds", {})
    verdict = "通过" if thresholds.get("passed") else "存在超阈值项"
    print(f"\n阈值判定（advisory={thresholds.get('advisory')}）：{verdict}")
    for violation in thresholds.get("violations", [])[:20]:
        print(
            f"  - {violation['scenario']} {violation['field']}: "
            f"实际 {violation['actual']} / 阈值 {violation['limit']}"
        )
    if thresholds.get("unmatched_patterns"):
        print(f"  未匹配到任何场景的阈值模式：{thresholds['unmatched_patterns']}")
    if thresholds.get("noisy_environment"):
        print(f"  注意：{thresholds.get('note')}")
