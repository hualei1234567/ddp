"""Spool 管理器：落盘流程、原子目录迁移、崩溃恢复与原始副本保护（FR-007）。"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edge_agent.config import SpoolConfig
from edge_agent.durable_fs import (
    atomic_rename_dir,
    copy_file_durable,
    fsync_dir,
    sha256_file,
    write_bytes_durable,
)
from edge_agent.errors import (
    ManifestError,
    PurgeNotAllowedError,
    SpoolIngestError,
    SpoolIntegrityError,
)
from edge_agent.faults import CrashHook, no_crash
from edge_agent.manifest.builder import build_manifest, load_manifest
from edge_agent.manifest.models import ArtifactInfo, Manifest, ManifestContext, ManifestWarning
from edge_agent.manifest.validation import validate_relative_path
from edge_agent.spool.models import (
    STATE_SUBDIR,
    RecoveryReport,
    ServerReceipt,
    SpoolArtifactRecord,
    SpoolEntry,
    SpoolState,
)
from edge_agent.spool.store import SpoolStore
from edge_agent.timesource import Clock, SystemClock


@dataclass(frozen=True)
class SourceFileSpec:
    """入 Spool 的单个源文件描述。"""

    source_path: Path
    rel_path: str
    mime_type: str
    format: str
    duration_ms: int | None = None


class SpoolManager:
    """管理本地 Spool 目录与 SQLite 状态机。"""

    def __init__(
        self,
        config: SpoolConfig,
        *,
        store: SpoolStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock: Clock = clock or SystemClock()
        self._ensure_layout()
        self._store = store or SpoolStore(config.state_db_path)

    @property
    def config(self) -> SpoolConfig:
        return self._config

    @property
    def store(self) -> SpoolStore:
        return self._store

    def close(self) -> None:
        self._store.close()

    def _ensure_layout(self) -> None:
        self._config.root.mkdir(parents=True, exist_ok=True)
        for subdir in STATE_SUBDIR.values():
            (self._config.root / subdir).mkdir(parents=True, exist_ok=True)
        fsync_dir(self._config.root)

    def entry_dir(self, entry_id: str, state: SpoolState) -> Path:
        return self._config.root / STATE_SUBDIR[state] / entry_id

    def payload_path(self, entry: SpoolEntry, rel_path: str) -> Path:
        safe_rel = validate_relative_path(rel_path, "rel_path")
        return self.entry_dir(entry.entry_id, entry.state) / "payload" / safe_rel

    def manifest_path(self, entry: SpoolEntry) -> Path:
        return self.entry_dir(entry.entry_id, entry.state) / "manifest.json"

    def ingest(
        self,
        context: ManifestContext,
        files: Sequence[SourceFileSpec],
        *,
        warnings: Sequence[ManifestWarning] = (),
        metadata: Mapping[str, Any] | None = None,
        entry_id: str | None = None,
        crash_hook: CrashHook = no_crash,
    ) -> SpoolEntry:
        """临时目录写完 -> fsync -> SHA256 -> Metadata+Manifest -> 原子移至 pending。"""
        if not files:
            raise SpoolIngestError("至少需要提供一个文件写入 Spool")
        eid = entry_id or str(uuid.uuid4())
        now = self._clock.now()
        self._store.create_tmp_entry(
            entry_id=eid,
            capture_session_id=context.capture_session_id,
            collection_task_id=context.collection_task_id,
            project_id=context.project_id,
            now=now,
        )
        tmp_dir = self.entry_dir(eid, SpoolState.TMP)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        artifacts, records = self._copy_and_hash_sources(tmp_dir, files)
        crash_hook("after_copy")

        manifest = build_manifest(context, artifacts, warnings=warnings)
        self._write_metadata_and_manifest(tmp_dir, eid, manifest, metadata)
        self._store.record_manifest_and_artifacts(
            eid, manifest_hash=manifest.manifest_hash, artifacts=records, now=self._clock.now()
        )
        crash_hook("after_manifest")

        pending_dir = self.entry_dir(eid, SpoolState.PENDING)
        atomic_rename_dir(tmp_dir, pending_dir)
        crash_hook("after_rename_pending")

        return self._store.transition(
            eid, SpoolState.PENDING, expected_state=SpoolState.TMP, now=self._clock.now()
        )

    def mark_uploading(self, entry_id: str) -> SpoolEntry:
        entry = self._store.get_entry(entry_id)
        if entry.state == SpoolState.UPLOADING:
            return entry
        self._move_entry_dir(entry_id, SpoolState.PENDING, SpoolState.UPLOADING)
        return self._store.transition(
            entry_id, SpoolState.UPLOADING, expected_state=SpoolState.PENDING, now=self._clock.now()
        )

    def requeue_pending(self, entry_id: str) -> SpoolEntry:
        entry = self._store.get_entry(entry_id)
        self._move_entry_dir(entry_id, entry.state, SpoolState.PENDING)
        return self._store.transition(
            entry_id, SpoolState.PENDING, expected_state=entry.state, now=self._clock.now()
        )

    def mark_uploaded(self, entry_id: str, receipt: ServerReceipt) -> SpoolEntry:
        entry = self._store.get_entry(entry_id)
        self._verify_server_receipt(entry, receipt)
        self._move_entry_dir(entry_id, SpoolState.UPLOADING, SpoolState.UPLOADED)
        return self._store.transition(
            entry_id,
            SpoolState.UPLOADED,
            expected_state=SpoolState.UPLOADING,
            server_receipt=receipt,
            now=self._clock.now(),
        )

    def mark_failed(self, entry_id: str, reason: str) -> SpoolEntry:
        entry = self._store.get_entry(entry_id)
        self._move_if_present(entry_id, entry.state, SpoolState.FAILED)
        return self._store.transition(
            entry_id,
            SpoolState.FAILED,
            expected_state=entry.state,
            failure_reason=reason,
            now=self._clock.now(),
        )

    def mark_quarantined(self, entry_id: str, reason: str) -> SpoolEntry:
        entry = self._store.get_entry(entry_id)
        self._move_if_present(entry_id, entry.state, SpoolState.QUARANTINED)
        return self._store.transition(
            entry_id,
            SpoolState.QUARANTINED,
            expected_state=entry.state,
            quarantine_reason=reason,
            now=self._clock.now(),
        )

    def purge(self, entry_id: str) -> None:
        """默认禁止删除唯一原始副本；仅在显式配置允许且已获服务端回执的 UPLOADED 状态才允许清理。"""
        if not self._config.allow_purge_uploaded:
            raise PurgeNotAllowedError("默认禁止删除 Spool 原始副本（allow_purge_uploaded=False）")
        entry = self._store.get_entry(entry_id)
        if entry.state != SpoolState.UPLOADED or entry.server_receipt is None:
            raise PurgeNotAllowedError(
                f"条目 {entry_id} 状态为 {entry.state}，未拿到完整服务端回执，禁止删除"
            )
        target = self.entry_dir(entry_id, SpoolState.UPLOADED)
        if target.exists():
            shutil.rmtree(target)
            fsync_dir(target.parent)
        self._store.delete_entry_record(entry_id)

    def verify_entry_integrity(self, entry: SpoolEntry) -> Manifest:
        """校验落盘 manifest 与全部 payload 文件的 SHA256 与大小。"""
        base = self.entry_dir(entry.entry_id, entry.state)
        return self._verify_dir_integrity(base, expected_manifest_hash=entry.manifest_hash)

    def recover(self) -> RecoveryReport:
        """启动/重启崩溃恢复：对账磁盘目录与 SQLite 状态，绝不自动删除已落盘的唯一原始副本。"""
        cleaned_tmp: list[str] = []
        promoted_pending: list[str] = []
        resumable_uploading: list[str] = []
        quarantined: list[str] = []
        orphan_cleaned: list[str] = []

        known_ids: set[str] = set()
        for entry in self._store.list_entries():
            known_ids.add(entry.entry_id)
            if entry.state == SpoolState.TMP:
                self._recover_tmp_entry(entry, cleaned_tmp, promoted_pending, quarantined)
            elif entry.state == SpoolState.UPLOADING:
                self._recover_uploading_entry(entry, resumable_uploading, quarantined)
            elif entry.state == SpoolState.PENDING:
                self._recover_pending_entry(entry, quarantined)

        tmp_root = self._config.root / STATE_SUBDIR[SpoolState.TMP]
        for child in sorted(tmp_root.iterdir()):
            if child.name not in known_ids:
                shutil.rmtree(child)
                fsync_dir(tmp_root)
                orphan_cleaned.append(child.name)

        return RecoveryReport(
            cleaned_tmp_ids=tuple(cleaned_tmp),
            promoted_pending_ids=tuple(promoted_pending),
            resumable_uploading_ids=tuple(resumable_uploading),
            quarantined_ids=tuple(quarantined),
            orphan_dirs_cleaned=tuple(orphan_cleaned),
        )

    def _recover_tmp_entry(
        self,
        entry: SpoolEntry,
        cleaned_tmp: list[str],
        promoted_pending: list[str],
        quarantined: list[str],
    ) -> None:
        tmp_dir = self.entry_dir(entry.entry_id, SpoolState.TMP)
        pending_dir = self.entry_dir(entry.entry_id, SpoolState.PENDING)
        if pending_dir.exists():
            try:
                manifest = self._verify_dir_integrity(pending_dir, entry.manifest_hash)
            except (ManifestError, SpoolIntegrityError) as exc:
                self._move_if_present(entry.entry_id, SpoolState.PENDING, SpoolState.QUARANTINED)
                self._store.transition(
                    entry.entry_id,
                    SpoolState.QUARANTINED,
                    expected_state=SpoolState.TMP,
                    quarantine_reason=f"recovery_corrupt_pending:{exc}",
                    now=self._clock.now(),
                )
                quarantined.append(entry.entry_id)
                return
            self._sync_artifacts_from_manifest(entry.entry_id, manifest)
            self._store.transition(
                entry.entry_id,
                SpoolState.PENDING,
                expected_state=SpoolState.TMP,
                now=self._clock.now(),
            )
            promoted_pending.append(entry.entry_id)
            return

        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
            fsync_dir(tmp_dir.parent)
        self._store.transition(
            entry.entry_id,
            SpoolState.FAILED,
            expected_state=SpoolState.TMP,
            failure_reason="incomplete_tmp_cleaned_on_recovery",
            now=self._clock.now(),
        )
        cleaned_tmp.append(entry.entry_id)

    def _recover_uploading_entry(
        self,
        entry: SpoolEntry,
        resumable_uploading: list[str],
        quarantined: list[str],
    ) -> None:
        uploading_dir = self.entry_dir(entry.entry_id, SpoolState.UPLOADING)
        pending_dir = self.entry_dir(entry.entry_id, SpoolState.PENDING)
        if not uploading_dir.exists() and pending_dir.exists():
            atomic_rename_dir(pending_dir, uploading_dir)
        try:
            self._verify_dir_integrity(uploading_dir, entry.manifest_hash)
        except (ManifestError, SpoolIntegrityError) as exc:
            self._move_if_present(entry.entry_id, SpoolState.UPLOADING, SpoolState.QUARANTINED)
            self._store.transition(
                entry.entry_id,
                SpoolState.QUARANTINED,
                expected_state=SpoolState.UPLOADING,
                quarantine_reason=f"recovery_corrupt_uploading:{exc}",
                now=self._clock.now(),
            )
            quarantined.append(entry.entry_id)
            return
        resumable_uploading.append(entry.entry_id)

    def _recover_pending_entry(self, entry: SpoolEntry, quarantined: list[str]) -> None:
        pending_dir = self.entry_dir(entry.entry_id, SpoolState.PENDING)
        try:
            self._verify_dir_integrity(pending_dir, entry.manifest_hash)
        except (ManifestError, SpoolIntegrityError) as exc:
            self._move_if_present(entry.entry_id, SpoolState.PENDING, SpoolState.QUARANTINED)
            self._store.transition(
                entry.entry_id,
                SpoolState.QUARANTINED,
                expected_state=SpoolState.PENDING,
                quarantine_reason=f"recovery_corrupt_pending:{exc}",
                now=self._clock.now(),
            )
            quarantined.append(entry.entry_id)

    def _copy_and_hash_sources(
        self, tmp_dir: Path, files: Sequence[SourceFileSpec]
    ) -> tuple[list[ArtifactInfo], list[SpoolArtifactRecord]]:
        artifacts: list[ArtifactInfo] = []
        records: list[SpoolArtifactRecord] = []
        for spec in files:
            safe_rel = validate_relative_path(spec.rel_path, "rel_path")
            if not spec.source_path.is_file():
                raise SpoolIngestError(f"源文件不存在或非普通文件：{spec.source_path}")
            before = spec.source_path.stat()
            if before.st_size <= 0:
                raise SpoolIngestError(f"源文件为空，拒绝写入 Spool：{spec.source_path}")
            dst = tmp_dir / "payload" / safe_rel
            copy_file_durable(spec.source_path, dst)
            after = spec.source_path.stat()
            if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                raise SpoolIngestError(f"源文件在复制期间发生变化：{spec.source_path}")
            digest = f"sha256:{sha256_file(dst)}"
            artifacts.append(
                ArtifactInfo(
                    path=safe_rel,
                    mime_type=spec.mime_type,
                    size_bytes=after.st_size,
                    sha256=digest,
                    format=spec.format,
                    duration_ms=spec.duration_ms,
                )
            )
            records.append(
                SpoolArtifactRecord(
                    rel_path=safe_rel,
                    mime_type=spec.mime_type.lower(),
                    size_bytes=after.st_size,
                    sha256=digest,
                    format=spec.format.upper(),
                    duration_ms=spec.duration_ms,
                )
            )
        return artifacts, records

    def _write_metadata_and_manifest(
        self,
        tmp_dir: Path,
        entry_id: str,
        manifest: Manifest,
        metadata: Mapping[str, Any] | None,
    ) -> None:
        write_bytes_durable(tmp_dir / "manifest.json", manifest.canonical_bytes)
        meta_doc = {
            "entry_id": entry_id,
            "manifest_hash": manifest.manifest_hash,
            "created_at": self._clock.now().isoformat(),
            "metadata": dict(metadata) if metadata else {},
        }
        meta_bytes = json.dumps(meta_doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
        write_bytes_durable(tmp_dir / "metadata.json", meta_bytes)

    def _verify_dir_integrity(self, base: Path, expected_manifest_hash: str | None) -> Manifest:
        manifest_file = base / "manifest.json"
        if not manifest_file.is_file():
            raise SpoolIntegrityError(f"缺少 manifest.json：{base}")
        manifest = load_manifest(manifest_file.read_bytes())
        if expected_manifest_hash is not None and manifest.manifest_hash != expected_manifest_hash:
            raise SpoolIntegrityError("磁盘 manifest_hash 与 SQLite 记录不一致")
        for item in manifest.document["artifacts"]:
            payload = base / "payload" / item["path"]
            if not payload.is_file():
                raise SpoolIntegrityError(f"Spool 缺少 payload 文件：{payload}")
            if payload.stat().st_size != item["size_bytes"]:
                raise SpoolIntegrityError(f"payload 大小不匹配：{payload}")
            actual = f"sha256:{sha256_file(payload)}"
            if actual != item["sha256"]:
                raise SpoolIntegrityError(f"payload SHA256 不匹配：{payload}")
        return manifest

    def _sync_artifacts_from_manifest(self, entry_id: str, manifest: Manifest) -> None:
        records = [
            SpoolArtifactRecord(
                rel_path=item["path"],
                mime_type=item["mime_type"],
                size_bytes=int(item["size_bytes"]),
                sha256=item["sha256"],
                format=item["format"],
                duration_ms=item.get("duration_ms"),
            )
            for item in manifest.document["artifacts"]
        ]
        self._store.record_manifest_and_artifacts(
            entry_id, manifest_hash=manifest.manifest_hash, artifacts=records, now=self._clock.now()
        )

    def _verify_server_receipt(self, entry: SpoolEntry, receipt: ServerReceipt) -> None:
        by_path = {item.artifact_path: item for item in receipt.artifacts}
        if len(by_path) != len(entry.artifacts):
            raise SpoolIntegrityError("服务端回执 artifact 数量与 Spool 条目不一致")
        for art in entry.artifacts:
            rec = by_path.get(art.rel_path)
            if rec is None or not rec.raw_asset_id:
                raise SpoolIntegrityError(f"服务端回执缺少 raw_asset_id：{art.rel_path}")
            if rec.sha256 != art.sha256:
                raise SpoolIntegrityError(f"服务端回执 SHA256 不一致：{art.rel_path}")

    def _move_entry_dir(self, entry_id: str, from_state: SpoolState, to_state: SpoolState) -> None:
        src = self.entry_dir(entry_id, from_state)
        dst = self.entry_dir(entry_id, to_state)
        if not src.exists():
            raise SpoolIntegrityError(f"Spool 目录缺失：{src}")
        atomic_rename_dir(src, dst)

    def _move_if_present(self, entry_id: str, from_state: SpoolState, to_state: SpoolState) -> None:
        src = self.entry_dir(entry_id, from_state)
        dst = self.entry_dir(entry_id, to_state)
        if src.exists() and not dst.exists():
            atomic_rename_dir(src, dst)
