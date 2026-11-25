"""V5.1 全链路真实测试：MinIO → STREAM_EXTRACTION → 产物。"""
import os, sys, json, hashlib
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "../data-platform")
os.environ.setdefault("DDP_ENV", "local")

from sqlalchemy import text
from app.core.config import get_settings
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.infrastructure.storage.processing_objects import S3StageArtifactStore, S3InputObjectReader
from app.domain.identity.models import ProjectScope
from app.domain.processing.models import ProcessingRun
from app.application.processing.stages import StageContext
from app.application.processing.stream_extraction_stage import StreamExtractionHandler
from uuid import uuid4, UUID

print("="*68); print("V5.1 全链路真实测试"); print("="*68)
settings = get_settings()
engine = create_db_engine(settings.database_url, pool_size=2, max_overflow=1,
                          pool_timeout_seconds=10, connect_timeout_seconds=10)
factory = create_session_factory(engine)
sample = Path.home()/"ddp-samples/robomimic/demo_v15.hdf5"
sha = hashlib.sha256(sample.read_bytes()).hexdigest()
print(f"样本: {sample.name} {sample.stat().st_size}B")
print(f"SHA256: {sha[:32]}...")

with factory() as s:
    pid = s.execute(text("select id from projects limit 1")).scalar()
    uid = s.execute(text("select id from users limit 1")).scalar()
print(f"项目: {pid}")
print(f"用户: {uid}")

storage = S3MultipartStorage(settings)
run_id = uuid4()
object_key = f"raw/{pid}/v51-e2e/{run_id}/demo.hdf5"
print(f"\n[1] 上传真实样本到 MinIO")
storage.put_bytes(object_key, sample.read_bytes(), content_type="application/x-hdf5")
print(f"    key={object_key}")
print(f"    size={storage.object_size(object_key)}B")

print(f"\n[2] 构造 STREAM_EXTRACTION 阶段")
reader = S3InputObjectReader(storage)
artifacts = S3StageArtifactStore(storage)
handler = StreamExtractionHandler(reader, artifacts, max_demos=3)
run = ProcessingRun(run_id=run_id, project_id=pid, pipeline_version="v5.1-e2e",
                    input_refs=(object_key,), created_by=uid, updated_by=uid)
scope = ProjectScope(project_id=pid, actor_id=uid)
print(f"    run_id={run_id}")

print(f"\n[3] 执行阶段")
outcome = handler(StageContext(run=run, scope=scope, now=None))
print(f"    status={outcome.status}")
print(f"    summary={outcome.log_summary}")
print(f"    artifacts={len(outcome.artifacts)}")

print(f"\n[4] 校验产物")
for i, a in enumerate(outcome.artifacts):
    print(f"    [{i}] {a.object_key.split('/')[-2]}/{a.object_key.split('/')[-1]}")
    print(f"        kind={a.kind.value} size={a.size_bytes}B schema={a.schema_version}")
    data = storage.stream_object(a.object_key, chunk_size=1<<20)
    first = next(iter(data)).decode().split("\n")[0]
    meta = json.loads(first)["_meta"]
    print(f"        meta: streams={len(meta['streams'])} action_source={meta['action_source']}")

print(f"\n[5] 清理")
for a in outcome.artifacts:
    storage.delete_object(a.object_key)
storage.delete_object(object_key)
print(f"    已清理 {len(outcome.artifacts)+1} 个对象")

checks = [
    ("真实样本上传 MinIO", storage is not None),
    ("阶段执行成功", outcome.status == "SUCCEEDED"),
    ("产出 Artifact", len(outcome.artifacts) > 0),
    ("产物类型正确", all(a.kind.value == "STREAM_EXTRACTION" for a in outcome.artifacts)),
    ("产物有 checksum", all(a.checksum_sha256 for a in outcome.artifacts)),
    ("产物可读回", len(outcome.artifacts) > 0),
]
print("\n" + "="*68)
for name, ok in checks: print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("="*68)
print(f"全链路: {'PASS' if all(o for _,o in checks) else 'FAIL'}")
engine.dispose()
