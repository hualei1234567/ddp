#!/usr/bin/env python3
"""DDP-308 用最小 S3 兼容协议替身（显式标注：非 MinIO、非 s3rver）。

仅实现阶段 2 上传适配器通过 boto3 使用到的 S3 协议子集：
CreateMultipartUpload、UploadPart、ListParts、CompleteMultipartUpload、
AbortMultipartUpload、HeadObject、GetObject(Range)、DeleteObject。
不校验签名、不做持久化、不模拟 MinIO 一致性/生命周期/权限行为。

用途：在本机 MinIO 镜像与 s3rver 3.7.1 均不可得时，补充验证
“API → boto3 → HTTP 预签名分片 → ListParts → Complete → Range GET”
的协议路径。MinIO 专属结论仍必须标 BLOCKED。
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree


@dataclass
class _Part:
    data: bytes
    etag: str


@dataclass
class _Upload:
    bucket: str
    key: str
    upload_id: str
    parts: dict[int, _Part] = field(default_factory=dict)
    completed: bool = False


class S3Stub:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._uploads: dict[str, _Upload] = {}
        self._objects: dict[str, bytes] = {}
        self._events: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        stub = self

        class Handler(_S3Handler):
            owner = stub

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------ 测试观察
    def object_exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects

    def object_size(self, key: str) -> int | None:
        with self._lock:
            value = self._objects.get(key)
            return None if value is None else len(value)

    def upload_count(self) -> int:
        with self._lock:
            return len(self._uploads)

    def log(self, message: str) -> None:
        with self._lock:
            self._events.append(message)

    def events(self) -> list[str]:
        with self._lock:
            return list(self._events)

    # ------------------------------------------------------------ 内部操作
    def _create_upload(self, bucket: str, key: str) -> str:
        upload_id = uuid.uuid4().hex
        with self._lock:
            self._uploads[upload_id] = _Upload(bucket=bucket, key=key, upload_id=upload_id)
        return upload_id

    def _put_part(self, bucket: str, key: str, upload_id: str, number: int, data: bytes) -> str:
        etag = hashlib.md5(data, usedforsecurity=False).hexdigest()  # noqa: S324 - 模拟 ETag
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None or upload.bucket != bucket or upload.key != key:
                raise KeyError("NoSuchUpload")
            if upload.completed:
                raise KeyError("NoSuchUpload")
            upload.parts[number] = _Part(data=data, etag=etag)
        return etag

    def _list_parts(self, bucket: str, key: str, upload_id: str) -> list[tuple[int, _Part]]:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None or upload.bucket != bucket or upload.key != key:
                raise KeyError("NoSuchUpload")
            if upload.completed:
                raise KeyError("NoSuchUpload")
            return sorted(upload.parts.items())

    def _complete(
        self,
        bucket: str,
        key: str,
        upload_id: str,
        requested: list[tuple[int, str]],
    ) -> str:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None or upload.bucket != bucket or upload.key != key:
                # 已完成的上传按 S3 语义返回 NoSuchUpload；适配器会用 HeadObject 判定幂等。
                raise KeyError("NoSuchUpload")
            stored = {number: part.etag for number, part in upload.parts.items()}
            normalized = {number: etag.strip('"') for number, etag in requested}
            if stored != normalized:
                raise ValueError("InvalidPart")
            content = b"".join(upload.parts[number].data for number in sorted(upload.parts))
            self._objects[key] = content
            upload.completed = True
            return hashlib.md5(content, usedforsecurity=False).hexdigest()  # noqa: S324

    def _abort(self, bucket: str, key: str, upload_id: str) -> None:
        with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is not None and upload.bucket == bucket and upload.key == key:
                upload.parts.clear()
                upload.completed = True

    def _head(self, key: str) -> tuple[int, str] | None:
        with self._lock:
            content = self._objects.get(key)
            if content is None:
                return None
            return len(content), hashlib.md5(content, usedforsecurity=False).hexdigest()  # noqa: S324

    def _get(self, key: str, start: int | None, end: int | None) -> tuple[int, bytes] | None:
        with self._lock:
            content = self._objects.get(key)
            if content is None:
                return None
            if start is None:
                return 200, content
            last = len(content) - 1 if end is None else min(end, len(content) - 1)
            return 206, content[start : last + 1]

    def _delete(self, key: str) -> None:
        with self._lock:
            self._objects.pop(key, None)


class _S3Handler(BaseHTTPRequestHandler):
    owner: S3Stub
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        return

    # ------------------------------------------------------------ 路由
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        bucket, key = self._bucket_key(parsed.path)
        if not key:
            self._send_xml(200, "<CreateBucketResult/>")
            return
        if "uploads" in query:
            upload_id = self.owner._create_upload(bucket, key)
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Bucket>{bucket}</Bucket><Key>{_xml_escape(key)}</Key>"
                f"<UploadId>{upload_id}</UploadId>"
                "</InitiateMultipartUploadResult>"
            )
            self._send_xml(200, body)
            return
        upload_id = _first(query, "uploadId")
        if upload_id:
            payload = self._read_body()
            try:
                requested = _parse_complete_parts(payload)
                etag = self.owner._complete(bucket, key, upload_id, requested)
            except KeyError:
                self._send_error(404, "NoSuchUpload", "The specified upload does not exist.")
                return
            except ValueError:
                self._send_error(400, "InvalidPart", "One or more specified parts could not be found.")
                return
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Location>http://{self.headers.get('Host', 'stub')}/{bucket}/{_xml_escape(key)}</Location>"
                f"<Bucket>{bucket}</Bucket><Key>{_xml_escape(key)}</Key>"
                f'<ETag>"{etag}"</ETag>'
                "</CompleteMultipartUploadResult>"
            )
            self._send_xml(200, body)
            return
        self._send_error(400, "InvalidRequest", "Unsupported POST request.")

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        bucket, key = self._bucket_key(parsed.path)
        if not key:
            self._send_empty(200)
            return
        upload_id = _first(query, "uploadId")
        part_number = _first(query, "partNumber")
        if upload_id and part_number:
            data = self._read_body()
            try:
                etag = self.owner._put_part(bucket, key, upload_id, int(part_number), data)
            except KeyError:
                self._send_error(404, "NoSuchUpload", "The specified upload does not exist.")
                return
            self.send_response(200)
            self.send_header("ETag", f'"{etag}"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_error(400, "InvalidRequest", "Unsupported PUT request.")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        bucket, key = self._bucket_key(parsed.path)
        if not key:
            self._send_xml(
                200,
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                "<Buckets><Bucket><Name>ddp-raw</Name>"
                "<CreationDate>2026-11-08T00:00:00.000Z</CreationDate></Bucket></Buckets>"
                "</ListAllMyBucketsResult>",
            )
            return
        upload_id = _first(query, "uploadId")
        if upload_id:
            try:
                parts = self.owner._list_parts(bucket, key, upload_id)
            except KeyError:
                self._send_error(404, "NoSuchUpload", "The specified upload does not exist.")
                return
            part_xml = "".join(
                "<Part>"
                f"<PartNumber>{number}</PartNumber>"
                f"<LastModified>{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')}</LastModified>"
                f'<ETag>"{part.etag}"</ETag>'
                f"<Size>{len(part.data)}</Size>"
                "</Part>"
                for number, part in parts
            )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListPartsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Bucket>{bucket}</Bucket><Key>{_xml_escape(key)}</Key>"
                f"<UploadId>{upload_id}</UploadId><PartNumberMarker>0</PartNumberMarker>"
                "<NextPartNumberMarker>0</NextPartNumberMarker><MaxParts>1000</MaxParts>"
                f"<IsTruncated>false</IsTruncated>{part_xml}</ListPartsResult>"
            )
            self._send_xml(200, body)
            return
        start, end = _parse_range(self.headers.get("Range"))
        result = self.owner._get(key, start, end)
        if result is None:
            self._send_error(404, "NoSuchKey", "The specified key does not exist.")
            return
        status, content = result
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Accept-Ranges", "bytes")
        if start is not None:
            total = self.owner.object_size(key) or len(content)
            last = start + len(content) - 1
            self.send_header("Content-Range", f"bytes {start}-{last}/{total}")
        self.end_headers()
        self.wfile.write(content)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        bucket, key = self._bucket_key(parsed.path)
        result = self.owner._head(key)
        if result is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        size, etag = result
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("ETag", f'"{etag}"')
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT"))
        self.end_headers()

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        bucket, key = self._bucket_key(parsed.path)
        upload_id = _first(query, "uploadId")
        if upload_id:
            self.owner._abort(bucket, key, upload_id)
            self._send_empty(204)
            return
        self.owner._delete(key)
        self._send_empty(204)

    # ------------------------------------------------------------ 工具
    def _bucket_key(self, path: str) -> tuple[str, str]:
        cleaned = path.lstrip("/")
        if not cleaned:
            return "", ""
        bucket, _, key = cleaned.partition("/")
        return unquote(bucket), unquote(key)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def _send_xml(self, status: int, body: str) -> None:
        self.owner.log(f"XML {status} {self.path.split('?', 1)[0]}?{self.path.partition('?')[2][:120]}")
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_error(self, status: int, code: str, message: str) -> None:
        self.owner.log(f"ERROR {code} status={status} {self.path.split('?', 1)[0]}?{self.path.partition('?')[2][:120]}")
        self._send_xml(
            status,
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Error><Code>{code}</Code><Message>{_xml_escape(message)}</Message></Error>",
        )


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _parse_range(value: str | None) -> tuple[int | None, int | None]:
    if not value or not value.startswith("bytes="):
        return None, None
    spec = value.removeprefix("bytes=").split(",", 1)[0]
    start_text, _, end_text = spec.partition("-")
    try:
        start = int(start_text)
        end = int(end_text) if end_text else None
    except ValueError:
        return None, None
    return start, end


def _parse_complete_parts(payload: bytes) -> list[tuple[int, str]]:
    root = ElementTree.fromstring(payload)
    parts: list[tuple[int, str]] = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "Part":
            continue
        number = None
        etag = None
        for child in item:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "PartNumber":
                number = int(child.text or "0")
            elif name == "ETag":
                etag = (child.text or "").strip()
        if number is None or etag is None:
            raise ValueError("InvalidPart")
        parts.append((number, etag))
    return parts


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
