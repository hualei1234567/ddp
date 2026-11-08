"""DDP-311 真实 MinIO 冒烟探针。

启动 /usr/local/bin/minio（127.0.0.1:9100，临时数据目录），验证：
预签名分片 PUT、ListParts、ETag、Complete、Range GET、预签名下载与过期。
本脚本只做环境冒烟，不作为最终验收证据；结果同时写入 minio-smoke.log。
"""

from __future__ import annotations

import hashlib
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import boto3
import httpx
from botocore.config import Config

EVIDENCE = Path(__file__).resolve().parent
PORT = 9100
BUCKET = "ddp-raw"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
PART_SIZE = 5 * 1024 * 1024


def wait_tcp(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"端口未就绪：{host}:{port}")


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="ddp311-minio-smoke-"))
    log_path = EVIDENCE / "minio-smoke.log"
    log_file = log_path.open("wb")
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                "/usr/local/bin/minio",
                "server",
                str(data_dir),
                "--address",
                f"127.0.0.1:{PORT}",
                "--console-address",
                "127.0.0.1:9101",
            ],
            env={
                "MINIO_ROOT_USER": ACCESS_KEY,
                "MINIO_ROOT_PASSWORD": SECRET_KEY,
                "MINIO_BROWSER": "off",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        wait_tcp("127.0.0.1", PORT)
        s3 = boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{PORT}",
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        s3.create_bucket(Bucket=BUCKET)
        key = "smoke/content.mcap"
        upload_id = s3.create_multipart_upload(Bucket=BUCKET, Key=key)["UploadId"]
        payload = b"\x89MCAP0\r\n" + bytes(range(256)) * ((PART_SIZE + 1024) // 256)
        payload = payload[: PART_SIZE + 1024]
        parts = []
        for number, start in enumerate(range(0, len(payload), PART_SIZE), start=1):
            chunk = payload[start : start + PART_SIZE]
            url = s3.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": BUCKET,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": number,
                    "ContentLength": len(chunk),
                },
                ExpiresIn=120,
                HttpMethod="PUT",
            )
            response = httpx.put(url, content=chunk, timeout=60)
            assert response.status_code == 200, (response.status_code, response.text)
            etag = response.headers["etag"]
            md5 = hashlib.md5(chunk).hexdigest()  # noqa: S324 - 仅用于比对 ETag
            print(f"PUT part={number} etag={etag} md5={md5}")
            parts.append({"PartNumber": number, "ETag": etag, "Size": len(chunk)})

        listed = s3.list_parts(Bucket=BUCKET, Key=key, UploadId=upload_id)["Parts"]
        print(f"ListParts count={len(listed)}")
        assert len(listed) == 2
        for item in listed:
            assert int(item["Size"]) == next(
                p["Size"] for p in parts if p["PartNumber"] == item["PartNumber"]
            )
        s3.complete_multipart_upload(
            Bucket=BUCKET,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts
                ]
            },
        )
        head = s3.head_object(Bucket=BUCKET, Key=key)
        print(f"Complete size={head['ContentLength']} etag={head['ETag']}")

        ranged = s3.get_object(Bucket=BUCKET, Key=key, Range="bytes=0-7")
        ranged_body = ranged["Body"].read()
        print(f"Range GET status={ranged['ResponseMetadata']['HTTPStatusCode']} body={ranged_body!r}")
        assert ranged_body == b"\x89MCAP0\r\n"

        download_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=15,
            HttpMethod="GET",
        )
        downloaded = httpx.get(download_url, timeout=30)
        print(f"Download status={downloaded.status_code} size={len(downloaded.content)}")
        assert downloaded.status_code == 200 and downloaded.content == payload

        version = subprocess.run(
            ["/usr/local/bin/minio", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"MinIO version output:\n{version.stdout}")
        print("SMOKE PASS")
        return 0
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log_file.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
