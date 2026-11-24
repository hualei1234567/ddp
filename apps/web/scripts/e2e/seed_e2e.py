#!/usr/bin/env python3
"""DDP 前端 E2E 种子：为关键路径与 RBAC 越权路径准备确定性数据。

**只写本机临时环境**：SQLite 临时文件 + 本机回环 MinIO 桶。脚本拒绝任何
非 loopback 数据库/对象存储地址，避免误连共享环境。

**只有这一套种子**：V51-013 的多角色/跨项目数据是对本脚本的扩展，不另起
第二套种子体系。所有角色、项目与资源都由本函数产出，Playwright 用例只引用
``main()`` 打印的那一行 JSON。

产出的固定 UUID 供 Playwright 用例引用（见 ``E2E_IDS``）：

- 项目 A ``11111111-...``（关键路径）+ 项目 B ``aaaaaaaa-...``（隔离对照）；
- 角色按仓库既有 ``ProjectRole``（viewer / editor / owner）分配，不新增角色：
  - A: ``88888888``(owner, 提交人) / ``99999999``(owner, 审核人) /
    ``12121212``(editor, 标注人) / ``13131313``(viewer, 只读)；
  - B: ``14141414``(owner，只在 B) —— A 的任何成员都不是 B 的成员；
- 采集任务 A ``22222222-...``、任务 B ``bbbbbbbb-...``；
- 数据源 ``33333333-...``、导入 ``44444444-...``、审核记录 ``55555555-...``；
- 标注 Schema ``66666666-...``（A）、``77777777-...``（B）；
- 数据集 ``bbbb...`` 见下方常量注释。

**为什么用真实 HTTP 推进任务状态**：``collection_tasks`` 有数据库触发器
（``trg_collection_tasks_initial_status`` / ``trg_collection_tasks_transition``），
直接 UPDATE 状态会被拒绝。走 API 既满足守卫，也顺带验证了状态机本身。

用法（由 ``scripts/e2e/run-local-stack.sh`` 调用）::

    DDP_DATABASE_URL=sqlite+pysqlite:////tmp/x.db \\
    DDP_E2E_API_BASE_URL=http://127.0.0.1:8010 \\
    backend/.venv/bin/python apps/web/scripts/e2e/seed_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PROJECT_ID = UUID("11111111-1111-7111-8111-111111111111")
SUBMITTER_ID = UUID("88888888-8888-7888-8888-888888888888")
REVIEWER_ID = UUID("99999999-9999-7999-8999-999999999999")
TASK_ID = UUID("22222222-2222-7222-8222-222222222222")
DATA_SOURCE_ID = UUID("33333333-3333-7333-8333-333333333333")
IMPORT_ID = UUID("44444444-4444-7444-8444-444444444444")
REVIEW_ID = UUID("55555555-5555-7555-8555-555555555555")

# --- V51-013 多角色 / 跨项目隔离种子 -------------------------------------
# 第二个项目的 UUID 与用户 ID 都选可读性极强的固定值：FAIL 时直接看出
# 报错里是"另一个项目"，不需要回查脚本。
PROJECT_B_ID = UUID("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa")
ANNOTATOR_ID = UUID("12121212-1212-7212-8212-121212121212")
VIEWER_ID = UUID("13131313-1313-7313-8313-131313131313")
OUTSIDER_ID = UUID("14141414-1414-7414-8414-141414141414")
TASK_B_ID = UUID("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb")
SCHEMA_A_ID = UUID("66666666-6666-7666-8666-666666666666")
SCHEMA_B_ID = UUID("77777777-7777-7777-8777-777777777777")
DATASET_A_ID = UUID("dddddddd-dddd-7ddd-8ddd-dddddddddddd")
DATASET_B_ID = UUID("eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee")
DATASET_VERSION_A_ID = UUID("d1d1d1d1-d1d1-7d1d-8d1d-d1d1d1d1d1d1")
DATASET_VERSION_B_ID = UUID("e1e1e1e1-e1e1-7e1e-8e1e-e1e1e1e1e1e1")
# 版本创建必须引用同项目的策略；四个策略各造一条（DRAFT 版本不要求报告）。
DEDUP_POLICY_A_ID = UUID("d2d2d2d2-d2d2-7d2d-8d2d-d2d2d2d2d2d2")
SPLIT_POLICY_A_ID = UUID("d3d3d3d3-d3d3-7d3d-8d3d-d3d3d3d3d3d3")
LEAKAGE_POLICY_A_ID = UUID("d4d4d4d4-d4d4-7d4d-8d4d-d4d4d4d4d4d4")
QUALITY_POLICY_A_ID = UUID("d5d5d5d5-d5d5-7d5d-8d5d-d5d5d5d5d5d5")

PROJECT_CODE = "ddp-e2e-n8-007"
PROJECT_NAME = "DDP E2E 关键路径项目"
PROJECT_B_CODE = "ddp-e2e-rbac-b"
PROJECT_B_NAME = "DDP E2E 隔离对照项目 B"
TASK_CODE = "E2E-TASK-001"
TASK_NAME = "E2E 采集任务（上传与动作条）"
TASK_B_CODE = "E2E-TASK-B"
TASK_B_NAME = "E2E 隔离对照任务 B"
DATA_SOURCE_CODE = "e2e-hf-source"
REVISION = "0" * 40

#: 输出的 ID 清单（JSON 一行），供 Playwright / 启动脚本读取。
E2E_IDS: dict[str, str] = {
    "project_id": str(PROJECT_ID),
    "project_b_id": str(PROJECT_B_ID),
    "submitter_id": str(SUBMITTER_ID),
    "reviewer_id": str(REVIEWER_ID),
    "annotator_id": str(ANNOTATOR_ID),
    "viewer_id": str(VIEWER_ID),
    "outsider_id": str(OUTSIDER_ID),
    "task_id": str(TASK_ID),
    "task_b_id": str(TASK_B_ID),
    "data_source_id": str(DATA_SOURCE_ID),
    "import_id": str(IMPORT_ID),
    "review_id": str(REVIEW_ID),
    "schema_a_id": str(SCHEMA_A_ID),
    "schema_b_id": str(SCHEMA_B_ID),
    "dataset_a_id": str(DATASET_A_ID),
    "dataset_b_id": str(DATASET_B_ID),
    "dataset_version_a_id": str(DATASET_VERSION_A_ID),
    "dataset_version_b_id": str(DATASET_VERSION_B_ID),
}


def e2e_ids() -> dict[str, str]:
    """返回 ID 清单的副本；调用方不得就地修改共享常量。"""
    return dict(E2E_IDS)


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "minio", "redis", "api"})


def _is_local_endpoint(value: str) -> bool:
    host = value
    if "://" in value:
        host = urlparse(value).hostname or ""
    elif "@" in value:
        host = value.rsplit("@", 1)[1].split(":")[0].split("/")[0]
    else:
        host = value.split(":")[0].split("/")[0]
    host = host.strip().lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _database_url() -> str:
    value = os.environ.get("DDP_DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("缺少 DDP_DATABASE_URL")

    prefix = "sqlite+pysqlite:///"
    database = value.removeprefix(prefix) if value.startswith(prefix) else ""
    database_path = Path(database)
    temp_roots = (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve())
    is_temporary_sqlite = (
        value.startswith(prefix)
        and database != ":memory:"
        and database_path.is_absolute()
        and any(database_path.resolve().is_relative_to(root) for root in temp_roots)
    )
    if not is_temporary_sqlite:
        raise SystemExit("拒绝使用非临时 E2E 数据库（仅允许临时目录中的 SQLite 文件）")
    return value


def _api_base_url() -> str:
    value = os.environ.get("DDP_E2E_API_BASE_URL", "").strip()
    if not value:
        raise SystemExit("缺少 DDP_E2E_API_BASE_URL")
    if not _is_local_endpoint(value):
        raise SystemExit("拒绝使用非本机后端（该脚本只用于本地 E2E）")
    return value.rstrip("/")


def _seed_identity(session: Session) -> None:
    from app.infrastructure.db.models.identity import Project, ProjectMember, User
    from sqlalchemy import select

    if session.get(Project, PROJECT_ID) is None:
        session.add(Project(id=PROJECT_ID, code=PROJECT_CODE, name=PROJECT_NAME))
    for user_id, name in (
        (SUBMITTER_ID, "E2E 提交人"),
        (REVIEWER_ID, "E2E 审核人"),
    ):
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=name))
    session.flush()
    for user_id in (SUBMITTER_ID, REVIEWER_ID):
        exists = session.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == PROJECT_ID,
                ProjectMember.user_id == user_id,
            )
        )
        if exists is None:
            session.add(
                ProjectMember(
                    project_id=PROJECT_ID,
                    user_id=user_id,
                    role="owner",
                )
            )


def _seed_task(session: Session) -> None:
    from app.infrastructure.db.models.collection import CollectionTask

    if session.get(CollectionTask, TASK_ID) is not None:
        return
    # 初始状态必须是 DRAFT（数据库触发器强制），后续经真实 HTTP 动作推进。
    session.add(
        CollectionTask(
            id=TASK_ID,
            project_id=PROJECT_ID,
            code=TASK_CODE,
            name=TASK_NAME,
            status="DRAFT",
            priority="P1",
            target_episode_count=10,
            target_duration_sec=600,
            created_by=SUBMITTER_ID,
            updated_by=SUBMITTER_ID,
        )
    )


def _seed_datasource(session: Session) -> None:
    from app.infrastructure.db.models.datasources import (
        DataSourceImportModel,
        DataSourceModel,
    )

    now = datetime.now(UTC)
    if session.get(DataSourceModel, DATA_SOURCE_ID) is None:
        session.add(
            DataSourceModel(
                id=DATA_SOURCE_ID,
                project_id=PROJECT_ID,
                code=DATA_SOURCE_CODE,
                name="E2E 公开数据集",
                kind="HUGGINGFACE",
                repo_id="lerobot/e2e_sample",
                revision=REVISION,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=SUBMITTER_ID,
                updated_by=SUBMITTER_ID,
                created_at=now,
                updated_at=now,
            )
        )
    if session.get(DataSourceImportModel, IMPORT_ID) is None:
        session.add(
            DataSourceImportModel(
                id=IMPORT_ID,
                project_id=PROJECT_ID,
                data_source_id=DATA_SOURCE_ID,
                status="PENDING",
                scope="SAMPLE",
                sample_limit=1,
                tool_name="e2e-import",
                tool_version="1.0.0",
                created_by=SUBMITTER_ID,
                updated_by=SUBMITTER_ID,
                created_at=now,
                updated_at=now,
            )
        )


def _seed_review(session: Session) -> None:
    from app.infrastructure.db.models.datasources import DataReviewModel

    if session.get(DataReviewModel, REVIEW_ID) is not None:
        return

    # 提交人 = SUBMITTER；E2E 用 REVIEWER 身份裁决，满足职责分离。
    session.add(
        DataReviewModel(
            id=REVIEW_ID,
            project_id=PROJECT_ID,
            import_id=IMPORT_ID,
            status="PENDING",
            decision=None,
            reason=None,
            reviewer_id=None,
            submitted_by=SUBMITTER_ID,
            created_by=SUBMITTER_ID,
            updated_by=SUBMITTER_ID,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )


def _seed_rbac_identity(session: Session) -> None:
    """V51-013：两个项目、四个角色的成员隔离。

    **不新增角色**：使用仓库既有 ``ProjectRole``（viewer / editor / owner），
    与后端 ``ROLE_PERMISSIONS`` 一一对应：
    - 项目 A：owner x2（提交人、审核人）/ editor（标注人）/ viewer（只读）；
    - 项目 B：仅 ``OUTSIDER_ID`` 一个 owner。A 的任何成员都不是 B 的成员，
      因此"拿 A 的身份去访问 B"必须被服务端拒绝。
    """
    from app.infrastructure.db.models.identity import Project, ProjectMember, User
    from sqlalchemy import select

    for project_id, code, name in (
        (PROJECT_ID, PROJECT_CODE, PROJECT_NAME),
        (PROJECT_B_ID, PROJECT_B_CODE, PROJECT_B_NAME),
    ):
        if session.get(Project, project_id) is None:
            session.add(Project(id=project_id, code=code, name=name))

    for user_id, name in (
        (ANNOTATOR_ID, "E2E 标注人（editor）"),
        (VIEWER_ID, "E2E 只读成员（viewer）"),
        (OUTSIDER_ID, "E2E 对照项目 B 负责人"),
    ):
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=name))
    session.flush()

    memberships = (
        (PROJECT_ID, SUBMITTER_ID, "owner"),
        (PROJECT_ID, REVIEWER_ID, "owner"),
        (PROJECT_ID, ANNOTATOR_ID, "editor"),
        (PROJECT_ID, VIEWER_ID, "viewer"),
        (PROJECT_B_ID, OUTSIDER_ID, "owner"),
    )
    for project_id, user_id, role in memberships:
        exists = session.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        if exists is None:
            session.add(ProjectMember(project_id=project_id, user_id=user_id, role=role))


def _seed_task_b(session: Session) -> None:
    """项目 B 的采集任务：用于"改 URL 里的 project_id 读不到别的项目"。"""
    from app.infrastructure.db.models.collection import CollectionTask

    if session.get(CollectionTask, TASK_B_ID) is not None:
        return
    session.add(
        CollectionTask(
            id=TASK_B_ID,
            project_id=PROJECT_B_ID,
            code=TASK_B_CODE,
            name=TASK_B_NAME,
            status="DRAFT",
            priority="P2",
            target_episode_count=1,
            target_duration_sec=60,
            created_by=OUTSIDER_ID,
            updated_by=OUTSIDER_ID,
        )
    )


def _seed_annotation_schema(session: Session) -> None:
    """两个项目各一条 DRAFT Schema：用于"猜 Schema ID 不能跨项目读取"。

    直接写库而不是走 HTTP：这里要的是**确定的固定 ID**，不是顺带验证创建
    路径；Schema 的 ``schema_sha256`` 由领域对象自行计算，不会造出非法行。
    """
    from app.domain.annotation.enums import (
        AnnotationLabelType,
        AnnotationLevel,
        LabelVisibility,
    )
    from app.domain.annotation.schema import AnnotationSchema, LabelDefinition
    from app.domain.identity.models import ProjectScope
    from app.infrastructure.repositories.annotation_schemas import (
        SqlAnnotationSchemaRepository,
    )

    rows = (
        (SCHEMA_A_ID, PROJECT_ID, "e2e-rbac-schema-a", "E2E RBAC Schema A", SUBMITTER_ID),
        (SCHEMA_B_ID, PROJECT_B_ID, "e2e-rbac-schema-b", "E2E RBAC Schema B", OUTSIDER_ID),
    )
    repository = SqlAnnotationSchemaRepository(session)
    now = datetime.now(UTC)
    for schema_id, project_id, code, name, actor in rows:
        scope = ProjectScope(project_id=project_id, actor_id=actor)
        if repository.get(scope, schema_id) is not None:
            continue
        repository.add(
            scope,
            AnnotationSchema(
                schema_id=schema_id,
                project_id=project_id,
                code=code,
                name=name,
                schema_lineage_id=schema_id,
                schema_version=1,
                description="V51-013 多角色浏览器 E2E 种子（只读断言用）。",
                annotation_levels=(AnnotationLevel.EPISODE,),
                labels=(
                    LabelDefinition(
                        label_key="episode_outcome",
                        label_type=AnnotationLabelType.ENUM,
                        required=True,
                        level=AnnotationLevel.EPISODE,
                        visibility=LabelVisibility.ANNOTATOR,
                        value_spec={"enum_values": ["success", "failure"]},
                    ),
                ),
                created_at=now,
                updated_at=now,
                created_by=actor,
                updated_by=actor,
            ),
        )


#: 项目 B 的策略 ID：只用于让 B 的版本引用「同项目」策略，用例不引用它们。
_POLICY_B_IDS = {
    "dedup": UUID("e2d2d2d2-d2d2-7d2d-8d2d-d2d2d2d2d2d2"),
    "split": UUID("e3d3d3d3-d3d3-7d3d-8d3d-d3d3d3d3d3d3"),
    "leakage": UUID("e4d4d4d4-d4d4-7d4d-8d4d-d4d4d4d4d4d4"),
    "quality": UUID("e5d5d5d5-d5d5-7d5d-8d5d-d5d5d5d5d5d5"),
}


def _seed_dataset(session: Session) -> None:
    """两个项目各一个数据集 + 一个 DRAFT 版本（含四条策略）。

    用于"DatasetVersion ID 猜测不可越权"：A 的成员拿 B 的 dataset_id /
    version_id 请求必须被服务端拒绝，而不是读出 B 的版本内容。
    """
    from app.domain.datasets.enums import (
        DatasetPurpose,
        DatasetStatus,
        DatasetUsagePolicy,
        DatasetVersionStatus,
        DedupKey,
        EmptySplitPolicy,
        GroupingKey,
        LeakageRule,
        RoundingRule,
        SplitStrategy,
        ValidityStatus,
    )
    from app.domain.datasets.models import Dataset, DatasetVersion
    from app.domain.datasets.policies import (
        DedupPolicy,
        LeakagePolicy,
        PolicyRef,
        QualityPolicy,
        SplitPolicy,
    )
    from app.domain.identity.models import ProjectScope
    from app.infrastructure.repositories.dataset_policy_repositories import (
        SqlDatasetPolicyRepository,
    )
    from app.infrastructure.repositories.dataset_repositories import (
        SqlDatasetRepository,
        SqlDatasetVersionRepository,
    )

    specs = (
        (
            PROJECT_ID,
            SUBMITTER_ID,
            DATASET_A_ID,
            DATASET_VERSION_A_ID,
            "e2e-rbac-ds-a",
            "E2E RBAC 数据集 A",
            {
                "dedup": DEDUP_POLICY_A_ID,
                "split": SPLIT_POLICY_A_ID,
                "leakage": LEAKAGE_POLICY_A_ID,
                "quality": QUALITY_POLICY_A_ID,
            },
        ),
        (
            PROJECT_B_ID,
            OUTSIDER_ID,
            DATASET_B_ID,
            DATASET_VERSION_B_ID,
            "e2e-rbac-ds-b",
            "E2E RBAC 数据集 B",
            _POLICY_B_IDS,
        ),
    )
    datasets = SqlDatasetRepository(session)
    policies = SqlDatasetPolicyRepository(session)
    versions = SqlDatasetVersionRepository(session)
    now = datetime.now(UTC)

    for project_id, actor, dataset_id, version_id, code, name, policy_ids in specs:
        scope = ProjectScope(project_id=project_id, actor_id=actor)
        if datasets.get(scope, dataset_id) is not None:
            continue
        datasets.add(
            scope,
            Dataset(
                dataset_id=dataset_id,
                project_id=project_id,
                dataset_lineage_id=dataset_id,
                code=code,
                name=name,
                purpose=DatasetPurpose.TRAINING,
                owner_id=actor,
                usage_policy=DatasetUsagePolicy.TRAINING_ALLOWED,
                description="V51-013 多角色浏览器 E2E 种子（只读断言用）。",
                status=DatasetStatus.DRAFT,
                created_at=now,
                updated_at=now,
                created_by=actor,
                updated_by=actor,
            ),
        )

        dedup = DedupPolicy(
            dedup_policy_id=policy_ids["dedup"],
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            exact_keys=(DedupKey.EPISODE_LINEAGE,),
        )
        split = SplitPolicy(
            split_policy_id=policy_ids["split"],
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            strategy=SplitStrategy.RATIO,
            seed=42,
            group_by=(GroupingKey.EPISODE_LINEAGE,),
            empty_split_policy=EmptySplitPolicy.ALLOW_EMPTY_TEST,
            rounding_rule=RoundingRule.LARGEST_REMAINDER,
        )
        leakage = LeakagePolicy(
            leakage_policy_id=policy_ids["leakage"],
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            hard_rules=(LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,),
            soft_rules=(),
            waiver_allowed_rules=(),
        )
        quality = QualityPolicy(
            quality_policy_id=policy_ids["quality"],
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
        )
        policies.add_dedup(scope, dedup)
        policies.add_split(scope, split)
        policies.add_leakage(scope, leakage)
        policies.add_quality(scope, quality)

        versions.add(
            scope,
            DatasetVersion(
                dataset_version_id=version_id,
                project_id=project_id,
                dataset_id=dataset_id,
                dataset_lineage_id=dataset_id,
                dataset_version=1,
                selection_rule_version="stage4-selection/1",
                selection_filters={},
                selection_snapshot_at=now,
                dedup_policy_ref=PolicyRef(
                    policy_id=dedup.dedup_policy_id,
                    version=dedup.version,
                    policy_sha256=dedup.policy_sha256 or "",
                ),
                split_policy_ref=PolicyRef(
                    policy_id=split.split_policy_id,
                    version=split.version,
                    policy_sha256=split.policy_sha256 or "",
                ),
                leakage_policy_ref=PolicyRef(
                    policy_id=leakage.leakage_policy_id,
                    version=leakage.version,
                    policy_sha256=leakage.policy_sha256 or "",
                ),
                quality_policy_ref=PolicyRef(
                    policy_id=quality.quality_policy_id,
                    version=quality.version,
                    policy_sha256=quality.policy_sha256 or "",
                ),
                status=DatasetVersionStatus.DRAFT,
                validity_status=ValidityStatus.VALID,
                created_at=now,
                updated_at=now,
                created_by=actor,
                updated_by=actor,
            ),
        )


def _advance_task_to_in_progress(api_base_url: str) -> None:
    """经真实 HTTP 动作把任务推进到 IN_PROGRESS（上传入口只在该状态开放）。

    每一步都带 ``If-Match`` 与 ``Idempotency-Key``；``approve`` 由审核人
    执行，避免"创建人审批自己的任务"被服务端拒绝。
    """
    import httpx

    base = f"{api_base_url}/api/v1/projects/{PROJECT_ID}/collection-tasks/{TASK_ID}"
    headers = {"X-User-Id": str(SUBMITTER_ID)}
    reviewer_headers = {"X-User-Id": str(REVIEWER_ID)}

    with httpx.Client(timeout=30.0) as client:
        detail = client.get(base, headers=headers)
        detail.raise_for_status()
        version = detail.json()["version"]
        if detail.json()["status"] == "IN_PROGRESS":
            return

        def act(action: str, *, actor_headers: dict[str, str], body: dict | None = None) -> None:
            nonlocal version
            response = client.post(
                f"{base}/actions/{action}",
                headers={
                    **actor_headers,
                    "If-Match": f'"{version}"',
                    "Idempotency-Key": f"e2e-{action}-{version}",
                },
                json=body or {},
            )
            response.raise_for_status()
            version = response.json()["version"]

        act("submit-review", actor_headers=headers)
        act("approve", actor_headers=reviewer_headers)
        act("ready", actor_headers=headers)
        act(
            "assign",
            actor_headers=headers,
            body={"assigned_to": str(SUBMITTER_ID)},
        )
        act("start", actor_headers=headers)


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    database_url = _database_url()
    api_base_url = _api_base_url()
    engine = create_engine(database_url)
    with Session(engine) as session:
        _seed_identity(session)
        _seed_task(session)
        _seed_datasource(session)
        _seed_review(session)
        # V51-013：同一套种子的多角色 / 跨项目扩展。
        _seed_rbac_identity(session)
        _seed_task_b(session)
        _seed_annotation_schema(session)
        _seed_dataset(session)
        session.commit()

    _advance_task_to_in_progress(api_base_url)

    print(json.dumps(e2e_ids(), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 种子失败必须让启动脚本显式失败
        print(f"E2E 种子失败：{exc}", file=sys.stderr)
        raise
