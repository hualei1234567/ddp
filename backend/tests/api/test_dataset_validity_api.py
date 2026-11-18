"""数据集版本有效性（validity_status 轴）：人工失效/恢复 + 上游巡检。

契约把 `validity_status` 定义为独立状态轴：`VALID→STALE` 由后台巡检在
上游失效/Manifest 变化时触发，`STALE→INVALIDATED` / `STALE→VALID` 由人工
执行并要求原因。此前没有任何写入点，这四个状态里只有一个可达。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.application.datasets.ports import SourceReachability
from app.application.datasets.validity import DatasetValiditySweep
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_directory import SqlDatasetUpstreamDirectory
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetItemRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.projects import SqlProjectMemberRepository
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)

SHA_EP = "sha256:" + "e" * 64


def _setup(factory: sessionmaker[Session], ids: ApiIds) -> dict[str, Any]:
    episodes = seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=10)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version = created.json()
    built = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/"
            f"{version['dataset_version_id']}/actions/build",
        ),
        headers={"If-Match": '"1"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text
    # 构建后是 VALIDATING，条目在 validate 阶段落库；巡检只扫 READY/FROZEN，
    # 所以这里走到 READY 再返回。
    validated = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/"
            f"{version['dataset_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={"reason": None},
    )
    assert validated.status_code == 200, validated.text
    return {
        "episodes": episodes,
        "editor": editor,
        "owner": owner,
        "dataset": dataset,
        "version": validated.json(),
    }


def _act(
    client: Any,
    project_id: UUID,
    dataset_id: str,
    version_id: str,
    action: str,
    version: int,
    *,
    reason: str | None = None,
) -> Any:
    return client.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/{action}"),
        headers={"If-Match": f'"{version}"', "Idempotency-Key": f"validity-{action}-{uuid4()}"},
        json={"reason": reason},
    )


def test_invalidate_requires_owner_and_reason(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    dataset_id = str(ctx["dataset"]["dataset_id"])
    version = ctx["version"]
    version_id = str(version["dataset_version_id"])

    # 契约：VALID 不能直接失效，必须先由巡检判定为 STALE（VALID→STALE 是
    # `system-mark-stale` 的边）。因此此时 allowed_actions 里不该出现 invalidate。
    assert "invalidate" not in version["allowed_actions"], version["allowed_actions"]

    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(version_id))
            .values(validity_status="STALE")
            .execution_options(synchronize_session=False)
        )
        session.commit()
    version = (
        ctx["editor"]
        .get(url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}"))
        .json()
    )
    assert "invalidate" in version["allowed_actions"], version["allowed_actions"]
    assert "revalidate" in version["allowed_actions"]

    forbidden = _act(
        ctx["editor"], ids.project_a, dataset_id, version_id, "invalidate", version["version"]
    )
    assert forbidden.status_code == 403

    missing_reason = _act(
        ctx["owner"],
        ids.project_a,
        dataset_id,
        version_id,
        "invalidate",
        version["version"],
    )
    assert missing_reason.status_code == 422
    assert missing_reason.json()["error"]["code"] == "validation_failed"

    invalidated = _act(
        ctx["owner"],
        ids.project_a,
        dataset_id,
        version_id,
        "invalidate",
        version["version"],
        reason="上游 Episode 被污染，版本不再可信",
    )
    assert invalidated.status_code == 200, invalidated.text
    body = invalidated.json()
    assert body["validity_status"] == "INVALIDATED"
    # INVALIDATED 是有效性终态：有效性轴不再下发动作；生命周期轴的动作仍在
    # （两根轴分离，失效不会把版本从生命周期里删掉）。
    assert "invalidate" not in body["allowed_actions"]
    assert "revalidate" not in body["allowed_actions"]

    # 失效必须用血缘边表达，而不是改历史数据。
    with factory() as session:
        edges = session.execute(
            select(DatasetVersionModel.id).where(DatasetVersionModel.id == UUID(version_id))
        ).all()
        assert edges
        from app.infrastructure.db.models.episodes import DataLineageEdgeModel

        invalidates = session.scalars(
            select(DataLineageEdgeModel).where(
                DataLineageEdgeModel.relation == "INVALIDATES",
                DataLineageEdgeModel.to_node_id == UUID(version_id),
            )
        ).all()
        assert len(invalidates) == len(ctx["episodes"])


def test_revalidate_restores_stale_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    dataset_id = str(ctx["dataset"]["dataset_id"])
    version_id = str(ctx["version"]["dataset_version_id"])

    # 模拟巡检已把版本标为 STALE（VALID 状态在契约里不允许直接 revalidate）。
    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(version_id))
            .values(validity_status="STALE")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    current = (
        ctx["editor"]
        .get(url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}"))
        .json()
    )
    assert current["validity_status"] == "STALE"
    assert "revalidate" in current["allowed_actions"]

    missing_reason = _act(
        ctx["editor"], ids.project_a, dataset_id, version_id, "revalidate", current["version"]
    )
    assert missing_reason.status_code == 422

    restored = _act(
        ctx["editor"],
        ids.project_a,
        dataset_id,
        version_id,
        "revalidate",
        current["version"],
        reason="已用显式版本重新校验通过",
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["validity_status"] == "VALID"

    # VALID 状态下 revalidate 不是合法迁移（不存在默认成功）。
    again = _act(
        ctx["editor"],
        ids.project_a,
        dataset_id,
        version_id,
        "revalidate",
        restored.json()["version"],
        reason="重复确认",
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"


def test_sweep_marks_stale_when_upstream_manifest_changes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = UUID(str(ctx["version"]["dataset_version_id"]))
    episode_id = ctx["episodes"][0]

    # 上游失效：条目引用的 Episode 被判 INVALIDATED（ACCEPTED→INVALIDATED 是
    # 状态机允许的迁移；Episode 的 manifest/内容字段不可变，不能靠改 Hash 模拟）。
    with factory() as session:
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.id == episode_id)
            .values(status="INVALIDATED")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    with factory() as session:
        sweep = DatasetValiditySweep(
            SqlDatasetVersionRepository(session),
            SqlDatasetItemRepository(session),
            SqlDatasetUpstreamDirectory(session),
            SqlProjectMemberRepository(session),
            SqlAuditRepository(session),
            SqlOutboxRepository(session),
            session,
        )
        outcomes = sweep.run_once(limit=50)

    assert [item.version_id for item in outcomes] == [version_id]
    assert any("失效" in reason for reason in outcomes[0].reasons), outcomes[0].reasons

    detail = (
        ctx["editor"]
        .get(url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}"))
        .json()
    )
    assert detail["validity_status"] == "STALE"
    # 有效性轴只剩人工决策：标记失效，或确认恢复（生命周期动作仍在）。
    assert {"invalidate", "revalidate"} <= set(detail["allowed_actions"])
    # 系统动作为后台推进用，绝不下发到界面。
    assert not any(a.startswith("system-") for a in detail["allowed_actions"]), detail[
        "allowed_actions"
    ]

    # 幂等：已经是 STALE 的版本不会被重复标记。
    with factory() as session:
        again = DatasetValiditySweep(
            SqlDatasetVersionRepository(session),
            SqlDatasetItemRepository(session),
            SqlDatasetUpstreamDirectory(session),
            SqlProjectMemberRepository(session),
            SqlAuditRepository(session),
            SqlOutboxRepository(session),
            session,
        ).run_once(limit=50)
    assert again == []


def test_sweep_leaves_healthy_version_valid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    with factory() as session:
        outcomes = DatasetValiditySweep(
            SqlDatasetVersionRepository(session),
            SqlDatasetItemRepository(session),
            SqlDatasetUpstreamDirectory(session),
            SqlProjectMemberRepository(session),
            SqlAuditRepository(session),
            SqlOutboxRepository(session),
            session,
        ).run_once(limit=50)
    assert outcomes == []
    detail = (
        ctx["editor"]
        .get(
            url(
                ids.project_a,
                f"/datasets/{ctx['dataset']['dataset_id']}/versions/"
                f"{ctx['version']['dataset_version_id']}",
            )
        )
        .json()
    )
    assert detail["validity_status"] == "VALID"


class _FakeSourceProbe:
    """来源探测端口替身：按固定结论应答并记录探测过的对象键。"""

    def __init__(self, verdict: SourceReachability) -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def probe(self, object_key: str) -> SourceReachability:
        self.calls.append(object_key)
        return self.verdict


def _sweep(
    session: Session,
    probe: _FakeSourceProbe,
    *,
    attempts: int = 1,
    max_failures: int = 5,
    max_objects: int = 200,
) -> DatasetValiditySweep:
    return DatasetValiditySweep(
        SqlDatasetVersionRepository(session),
        SqlDatasetItemRepository(session),
        SqlDatasetUpstreamDirectory(session),
        SqlProjectMemberRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
        source_probe=probe,
        probe_attempts=attempts,
        max_objects_per_version=max_objects,
        max_failures_per_run=max_failures,
    )


def _version_detail(ctx: dict[str, Any], ids: ApiIds, version_id: str) -> dict[str, Any]:
    return ctx["editor"].get(
        url(
            ids.project_a,
            f"/datasets/{ctx['dataset']['dataset_id']}/versions/{version_id}",
        )
    ).json()


def test_source_probe_reachable_keeps_version_valid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    version_id = str(ctx["version"]["dataset_version_id"])
    probe = _FakeSourceProbe(SourceReachability.REACHABLE)

    with factory() as session:
        outcomes = _sweep(session, probe).run_once(limit=50)

    assert outcomes == []
    # 确实探测了当前项目的 Episode Manifest，而且是服务端得到的作用域。
    assert probe.calls
    assert all(str(ids.project_a) in key for key in probe.calls)
    assert _version_detail(ctx, ids, version_id)["validity_status"] == "VALID"


def test_source_probe_missing_marks_unknown_idempotently(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    version_id = str(ctx["version"]["dataset_version_id"])
    probe = _FakeSourceProbe(SourceReachability.MISSING)

    with factory() as session:
        outcomes = _sweep(session, probe).run_once(limit=50)

    assert len(outcomes) == 1
    assert outcomes[0].version_id == UUID(version_id)
    assert any("不存在" in reason for reason in outcomes[0].reasons)
    detail = _version_detail(ctx, ids, version_id)
    assert detail["validity_status"] == "UNKNOWN"
    # UNKNOWN 是"无法确认"，不是已失效：负责人仍可标记失效。
    assert "invalidate" in detail["allowed_actions"]
    assert not any(action.startswith("system-") for action in detail["allowed_actions"])

    with factory() as session:
        again = _sweep(session, probe).run_once(limit=50)
    assert again == []
    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(AuditLog.action == "system-mark-unknown")
            )
        )
    assert len(audits) == 1


def test_source_probe_unreachable_keeps_valid_and_is_bounded(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """超时/存储不可用是"探测不到"，不得把 VALID 写坏；探测次数必须有界。"""
    factory, ids = api_db
    ctx = _setup(factory, ids)
    version_id = str(ctx["version"]["dataset_version_id"])
    probe = _FakeSourceProbe(SourceReachability.UNREACHABLE)

    with factory() as session:
        outcomes = _sweep(
            session,
            probe,
            attempts=2,
            max_failures=2,
        ).run_once(limit=50)

    assert outcomes == []
    # 首个对象重试 attempts 次后即放弃本版本，不再继续探测其余 Episode；
    # 失败计数由单轮预算约束，不会无限放大。
    assert len(probe.calls) == 2
    assert _version_detail(ctx, ids, version_id)["validity_status"] == "VALID"


def test_source_probe_recovery_requires_human_revalidate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    dataset_id = str(ctx["dataset"]["dataset_id"])
    version_id = str(ctx["version"]["dataset_version_id"])
    probe = _FakeSourceProbe(SourceReachability.MISSING)

    with factory() as session:
        assert len(_sweep(session, probe).run_once(limit=50)) == 1
    assert _version_detail(ctx, ids, version_id)["validity_status"] == "UNKNOWN"

    # 来源恢复可达：系统只推进到 STALE，不替人断言"内容仍然有效"。
    probe.verdict = SourceReachability.REACHABLE
    with factory() as session:
        outcomes = _sweep(session, probe).run_once(limit=50)
    assert len(outcomes) == 1
    assert any("恢复" in reason for reason in outcomes[0].reasons)
    detail = _version_detail(ctx, ids, version_id)
    assert detail["validity_status"] == "STALE"
    assert "revalidate" in detail["allowed_actions"]

    restored = _act(
        ctx["editor"],
        ids.project_a,
        dataset_id,
        version_id,
        "revalidate",
        int(detail["version"]),
        reason="来源恢复后人工确认",
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["validity_status"] == "VALID"
