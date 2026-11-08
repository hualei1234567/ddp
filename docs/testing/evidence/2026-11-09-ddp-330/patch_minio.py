#!/usr/bin/env python3
"""为 DDP-330 探针增加“临时真实 MinIO 实例”支持。

背景：本机 9000 端口 MinIO 凭据未注入，而 staging .env 禁止读取。
方案：使用真实 /usr/local/bin/minio 在独立端口与临时目录启动一次性 QA
实例；若外部提供 DDP330_S3_ENDPOINT/KEY/SECRET 则沿用外部实例。
不触碰任何现有桶、staging 数据或生产配置。
"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent / "run_ddp330.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"未命中补丁锚点：{label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import subprocess\nimport sys",
        "import subprocess\nimport sys\nimport tempfile",
        "tempfile 导入",
    )

    text = replace_once(
        text,
        'S3_ENDPOINT = "http://127.0.0.1:9000"\n'
        'S3_ACCESS_KEY = os.environ.get("DDP330_S3_ACCESS_KEY", "")\n'
        'S3_SECRET_KEY = os.environ.get("DDP330_S3_SECRET_KEY", "")\n'
        "if not S3_ACCESS_KEY or not S3_SECRET_KEY:\n"
        "    raise SystemExit(\n"
        '        "缺少本地 MinIO 凭据：请设置 DDP330_S3_ACCESS_KEY / DDP330_S3_SECRET_KEY"\n'
        "    )\n",
        'S3_PORT = int(os.environ.get("DDP330_S3_PORT", "9010"))\n'
        'S3_ENDPOINT = os.environ.get("DDP330_S3_ENDPOINT", f"http://127.0.0.1:{S3_PORT}")\n'
        'S3_ACCESS_KEY = os.environ.get("DDP330_S3_ACCESS_KEY", "")\n'
        'S3_SECRET_KEY = os.environ.get("DDP330_S3_SECRET_KEY", "")\n'
        "MANAGED_MINIO = not (S3_ACCESS_KEY and S3_SECRET_KEY)\n"
        'MINIO_DATA_DIR = Path(tempfile.gettempdir()) / (\n'
        '    f"ddp-qa-minio-{os.getpid()}-{int(time.time())}"\n'
        ")\n"
        "if MANAGED_MINIO:\n"
        '    S3_ACCESS_KEY = f"qa330{uuid4().hex[:12]}"\n'
        "    S3_SECRET_KEY = uuid4().hex + uuid4().hex\n",
        "MinIO 配置",
    )

    text = replace_once(
        text,
        "client: httpx.Client | None = None\nserver_process: subprocess.Popen[str] | None = None",
        "client: httpx.Client | None = None\n"
        "server_process: subprocess.Popen[str] | None = None\n"
        "minio_process: subprocess.Popen[str] | None = None",
        "MinIO 进程变量",
    )

    text = replace_once(
        text,
        "def ensure_qa_bucket() -> None:",
        '''def start_managed_minio() -> subprocess.Popen[str]:
    """启动一次性真实 MinIO；独立端口、独立临时目录、随机凭据。"""
    MINIO_DATA_DIR.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(
        {
            "MINIO_ROOT_USER": S3_ACCESS_KEY,
            "MINIO_ROOT_PASSWORD": S3_SECRET_KEY,
            "MINIO_BROWSER": "off",
        }
    )
    log_path = EVIDENCE / "minio.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            "/usr/local/bin/minio",
            "server",
            str(MINIO_DATA_DIR),
            "--address",
            f"127.0.0.1:{S3_PORT}",
            "--console-address",
            f"127.0.0.1:{S3_PORT + 1}",
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MinIO 提前退出，见 {log_path}")
        try:
            if httpx.get(f"{S3_ENDPOINT}/minio/health/live", timeout=1.0).status_code == 200:
                observe(
                    f"临时真实 MinIO 已启动：{S3_ENDPOINT}，数据目录 {MINIO_DATA_DIR}。"
                )
                return process
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    process.terminate()
    raise RuntimeError(f"MinIO 未能启动，见 {log_path}")


def stop_managed_minio() -> None:
    if minio_process is None:
        return
    minio_process.terminate()
    try:
        minio_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        minio_process.kill()


def ensure_qa_bucket() -> None:''',
        "MinIO 启动函数",
    )

    text = replace_once(
        text,
        "def main() -> int:\n    global client, server_process",
        "def main() -> int:\n    global client, server_process, minio_process",
        "main 全局声明",
    )

    text = replace_once(
        text,
        "        ensure_qa_bucket()\n",
        "        if MANAGED_MINIO:\n"
        "            minio_process = start_managed_minio()\n"
        "        ensure_qa_bucket()\n",
        "main 启动 MinIO",
    )

    text = replace_once(
        text,
        "        if server_process is not None:\n"
        "            server_process.terminate()\n"
        "            try:\n"
        "                server_process.wait(timeout=10)\n"
        "            except subprocess.TimeoutExpired:\n"
        "                server_process.kill()\n"
        "        write_outputs(extra)",
        "        if server_process is not None:\n"
        "            server_process.terminate()\n"
        "            try:\n"
        "                server_process.wait(timeout=10)\n"
        "            except subprocess.TimeoutExpired:\n"
        "                server_process.kill()\n"
        "        stop_managed_minio()\n"
        "        write_outputs(extra)",
        "main 停止 MinIO",
    )

    text = replace_once(
        text,
        '        "qa_bucket": QA_BUCKET,\n',
        '        "qa_bucket": QA_BUCKET,\n'
        '        "s3_endpoint": S3_ENDPOINT,\n'
        '        "managed_minio": MANAGED_MINIO,\n'
        '        "minio_data_dir": str(MINIO_DATA_DIR) if MANAGED_MINIO else None,\n',
        "输出 MinIO 信息",
    )

    TARGET.write_text(text, encoding="utf-8")
    print("临时真实 MinIO 支持已加入")


if __name__ == "__main__":
    main()
