#!/usr/bin/env python3
"""把 DDP-329 探针适配为 DDP-330 复验探针（基线 f4d32a4 / 迁移 head 0020）。

只改写 docs/testing/evidence 下的 QA 探针，不触碰业务代码。
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "run_ddp330.py"
PYTEST_TARGET = HERE / "run_pytest.py"

OLD_HEAD = "7c23099c7014d89a66429ad5c79400b4cb4c0382"
NEW_HEAD = "f4d32a4d004129ee1827d277f8d720ac74b2ab52"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"未命中补丁锚点：{label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    # 1) 全局 329 -> 330（库名、桶名、端口、环境变量、示例 ID 等）。
    text = text.replace("329", "330")
    text = text.replace(OLD_HEAD, NEW_HEAD)

    # 2) 所有 Episode 播种固定 robot_id/device_ids，供 HTTP 过滤真实命中。
    text = replace_once(
        text,
        'ADAPTER_ID = "qa-adapter"',
        'ROBOT_ID = UUID("11111111-1111-4111-8111-111111111111")\n'
        'DEVICE_ID = UUID("22222222-2222-4222-8222-222222222222")\n'
        'ADAPTER_ID = "qa-adapter"',
        "robot/device 常量",
    )
    text = replace_once(
        text,
        "                    robot_id=None,\n                    device_ids=[],",
        "                    robot_id=ROBOT_ID,\n                    device_ids=[str(DEVICE_ID)],",
        "Episode 播种 robot/device",
    )

    # 3) 0020 迁移：在 0019 库注入 stale 守卫后 upgrade 0020，验证重装。
    anchor = (
        '    final_stage4 = {item["trigger"] for item in stage4_trigger_inventory()}\n'
        "    check(\n"
        '        "D7",\n'
        '        "0019(head) 后阶段 4 触发器共 54 个且名单精确匹配",'
    )
    replacement = '''    # 模拟已部署的 0019 旧库：把外部训练引用守卫替换为错误使用 status 列的
    # stale 版本（真实旧缺陷），再执行 upgrade head(0020)，验证迁移重装守卫。
    with psycopg.connect(PG_DSN) as connection:
        connection.execute(
            """
            CREATE OR REPLACE FUNCTION ddp_external_training_reference_guard()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.status IS DISTINCT FROM NEW.status THEN
                    RAISE EXCEPTION 'stale_guard_status' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
        connection.commit()
        stale_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()[0]
    check(
        "D7",
        "0019 库已注入旧版 stale 守卫（使用 OLD.status）",
        "OLD.status" in stale_source,
        "含 OLD.status",
        {"has_wrong_status": "OLD.status" in stale_source},
    )

    command.upgrade(config, "0020")
    with psycopg.connect(PG_DSN) as connection:
        repaired_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()[0]
        trigger_state = connection.execute(
            "SELECT tgenabled FROM pg_trigger "
            "WHERE tgname = 'trg_external_training_references_transition' "
            "AND NOT tgisinternal"
        ).fetchone()
    check(
        "D7",
        "0019 库执行 upgrade head 到 0020 后 stale 守卫被重装为 external_status",
        "OLD.external_status" in repaired_source and "OLD.status" not in repaired_source,
        "含 OLD.external_status 且不含 OLD.status",
        {
            "has_external_status": "OLD.external_status" in repaired_source,
            "has_wrong_status": "OLD.status" in repaired_source,
            "revision": current_revision(),
        },
    )
    check(
        "D7",
        "0020 后外部训练引用转换触发器处于启用状态",
        bool(trigger_state) and trigger_state[0] == "O",
        "O",
        trigger_state[0] if trigger_state else None,
    )
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "0020",
            "actual": current_revision(),
            "stage4_triggers": stage4_trigger_inventory(),
            "guard_source_has_external_status": "OLD.external_status" in repaired_source,
            "transition_trigger_enabled": trigger_state[0] if trigger_state else None,
        }
    )

    final_stage4 = {item["trigger"] for item in stage4_trigger_inventory()}
    check(
        "D7",
        "0020(head) 后阶段 4 触发器共 54 个且名单精确匹配",'''
    text = replace_once(text, anchor, replacement, "0020 迁移复核")
    text = replace_once(
        text,
        '        "0019 后阶段 4 表全部存在",',
        '        "0020 后阶段 4 表全部存在",',
        "0020 表检查文案",
    )

    # 4) 降级链增加 0019：先降 0020（安全修复不回滚），再降 0019 删除训练表。
    text = replace_once(
        text,
        '    downgrade_expectations = {\n',
        '    downgrade_expectations = {\n'
        '        "0019": {"tables": STAGE4_TABLES, "triggers": 54},\n',
        "降级期望 0019",
    )
    text = replace_once(
        text,
        '    for revision in ("0018", "0017", "0016", "0015", "0014", "0013", "0012", "0011"):',
        '    for revision in ("0019", "0018", "0017", "0016", "0015", "0014", "0013", "0012", "0011"):',
        "降级链 0019",
    )
    text = replace_once(
        text,
        '        "降级后重新升级到 head(0019)",\n'
        '        current_revision() == "0019" and STAGE4_TABLES <= set(public_tables()),\n'
        '        {"revision": "0019", "tables": sorted(STAGE4_TABLES)},',
        '        "降级后重新升级到 head(0020)",\n'
        '        current_revision() == "0020" and STAGE4_TABLES <= set(public_tables()),\n'
        '        {"revision": "0020", "tables": sorted(STAGE4_TABLES)},',
        "重新升级 head 0020",
    )
    text = replace_once(
        text,
        '            "stage4_triggers": stage4_trigger_inventory(),\n'
        '        }\n'
        '    )\n'
        "\n\n"
        "# ---------------------------------------------------------------------------\n"
        "# 测试数据播种",
        '            "stage4_triggers": stage4_trigger_inventory(),\n'
        '        }\n'
        '    )\n'
        '    with psycopg.connect(PG_DSN) as connection:\n'
        "        head_source = connection.execute(\n"
        '            "SELECT prosrc FROM pg_proc "\n'
        '            "WHERE proname = \'ddp_external_training_reference_guard\'"\n'
        "        ).fetchone()[0]\n"
        "    check(\n"
        '        "D7",\n'
        '        "重新升级到 head 后外部训练引用守卫使用 external_status",\n'
        '        "OLD.external_status" in head_source and "OLD.status" not in head_source,\n'
        '        "含 OLD.external_status 且不含 OLD.status",\n'
        '        {\n'
        '            "has_external_status": "OLD.external_status" in head_source,\n'
        '            "has_wrong_status": "OLD.status" in head_source,\n'
        "        },\n"
        "    )\n"
        "\n\n"
        "# ---------------------------------------------------------------------------\n"
        "# 测试数据播种",
        "head guard 检查",
    )

    # 5) D2-1：episode_ids/robot_ids/device_ids 字符串 UUID 可用，非法 UUID 422。
    d2_anchor = '''    # 选择过滤的 episode_ids 是契约字段；HTTP JSON 传入字符串 UUID 应可用。
    episode_id_filter = preview_version(
        "editor_a",
        "project_a",
        dataset_id,
        version_id,
        policies,
        selection_filters={"episode_ids": [str(episodes["base"][0])]},
        area="D2",
    )
    check(
        "D2",
        "selection_filters.episode_ids 可通过 HTTP 使用",
        episode_id_filter.status_code == 200,
        200,
        episode_id_filter.status_code,
        severity="major",
        note="若 422，说明 HTTP JSON UUID 未转换为 UUID 对象，过滤能力与契约不一致",
        evidence={"body": _safe_json(episode_id_filter)},
    )
'''
    d2_replacement = '''    # 选择过滤的 UUID 字段是契约字段；HTTP JSON 传入字符串 UUID 应可用。
    for field_name, values in (
        ("episode_ids", [str(episodes["base"][0])]),
        ("robot_ids", [str(ROBOT_ID)]),
        ("device_ids", [str(DEVICE_ID)]),
    ):
        filtered = preview_version(
            "editor_a",
            "project_a",
            dataset_id,
            version_id,
            policies,
            selection_filters={field_name: values},
            area="D2",
        )
        filtered_body = _safe_json(filtered)
        check(
            "D2",
            f"selection_filters.{field_name} 字符串 UUID 可通过 HTTP 使用",
            filtered.status_code == 200
            and int(filtered_body.get("selected_count", 0)) >= 1,
            200,
            {
                "status": filtered.status_code,
                "selected_count": filtered_body.get("selected_count"),
            },
            severity="major",
            note="字符串 UUID 应被解析为 UUID，而不是 422",
            evidence={"body": filtered_body},
        )
    for field_name in ("episode_ids", "robot_ids", "device_ids"):
        invalid_uuid = preview_version(
            "editor_a",
            "project_a",
            dataset_id,
            version_id,
            policies,
            selection_filters={field_name: ["not-a-uuid"]},
        )
        expect_status(
            "D2",
            f"selection_filters.{field_name} 非法 UUID 返回 422",
            invalid_uuid,
            422,
        )
        expect_code(
            "D2",
            f"selection_filters.{field_name} 非法 UUID 错误码",
            invalid_uuid,
            "validation_failed",
        )
'''
    text = replace_once(text, d2_anchor, d2_replacement, "D2-1 复验")

    # 6) D6：RUNNING 回调后四类落库证据 + 幂等重放不新增记录。
    d6_anchor = '''    expect_eq("D6", "RUNNING 回调记录 model_ref", True, bool(status["model_ref"]))
'''
    d6_replacement = '''    expect_eq("D6", "RUNNING 回调记录 model_ref", True, bool(status["model_ref"]))
    with factory() as session:
        running_callback = session.execute(
            text(
                "SELECT nonce, payload_sha256 FROM training_adapter_callbacks "
                "WHERE adapter_id = :adapter_id AND event_id = :event_id"
            ),
            {"adapter_id": ADAPTER_ID, "event_id": event_running},
        ).fetchone()
        running_outbox = session.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback' "
                "AND payload->>'event_id' = :event_id"
            ),
            {"event_id": event_running},
        ).fetchone()
        running_callback_count = session.execute(
            text("SELECT count(*) FROM training_adapter_callbacks")
        ).scalar_one()
        running_outbox_count = session.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback'"
            )
        ).scalar_one()
    check(
        "D6",
        "RUNNING 回调防重放记录已落库",
        running_callback is not None and running_callback[0] == nonce_running,
        {"exists": True, "nonce": nonce_running},
        {
            "exists": running_callback is not None,
            "nonce": running_callback[0] if running_callback else None,
        },
    )
    check(
        "D6",
        "RUNNING 回调 outbox 已落库且记录 to_status=RUNNING",
        running_outbox is not None and running_outbox[0].get("to_status") == "RUNNING",
        {"exists": True, "to_status": "RUNNING"},
        {
            "exists": running_outbox is not None,
            "payload": running_outbox[0] if running_outbox else None,
        },
    )
'''
    text = replace_once(text, d6_anchor, d6_replacement, "D6 RUNNING 四类落库")

    d6_replay_anchor = '''    expect_status("D6", "同 event 同 payload 重放返回 204", replay, 204)
'''
    d6_replay_replacement = '''    expect_status("D6", "同 event 同 payload 重放返回 204", replay, 204)
    with factory() as session:
        replay_callback_count = session.execute(
            text("SELECT count(*) FROM training_adapter_callbacks")
        ).scalar_one()
        replay_outbox_count = session.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback'"
            )
        ).scalar_one()
    expect_eq(
        "D6",
        "同 event 同 payload 重放不新增回调记录",
        running_callback_count,
        replay_callback_count,
    )
    expect_eq(
        "D6",
        "同 event 同 payload 重放不新增 outbox",
        running_outbox_count,
        replay_outbox_count,
    )
'''
    text = replace_once(text, d6_replay_anchor, d6_replay_replacement, "D6 幂等不新增")

    # 7) D6-M1：非法 external_status -> 422 稳定业务错误码。
    d6_m1_anchor = '''    check(
        "D6",
        "非法 external_status 失败关闭",
        invalid_status.status_code == 422,
        422,
        invalid_status.status_code,
        severity="major",
        note="非枚举状态不应以未处理异常返回 500；应返回稳定业务错误码",
        evidence={"body": _safe_json(invalid_status)},
    )
'''
    d6_m1_replacement = '''    expect_status("D6", "非法 external_status 失败关闭", invalid_status, 422)
    expect_code(
        "D6",
        "非法 external_status 返回稳定业务错误码",
        invalid_status,
        "validation_failed",
    )
'''
    text = replace_once(text, d6_m1_anchor, d6_m1_replacement, "D6-M1 复验")

    # 8) SUCCEEDED 后补四类落库：状态/模型引用/回调表/outbox。
    d6_succeeded_anchor = '''    expect_eq("D6", "SUCCEEDED 回调记录 evaluation_ref", True, bool(status["evaluation_ref"]))
'''
    d6_succeeded_replacement = '''    expect_eq("D6", "SUCCEEDED 回调记录 evaluation_ref", True, bool(status["evaluation_ref"]))
    with factory() as session:
        succeeded_callback = session.execute(
            text(
                "SELECT nonce FROM training_adapter_callbacks "
                "WHERE adapter_id = :adapter_id AND event_id = :event_id"
            ),
            {"adapter_id": ADAPTER_ID, "event_id": event_succeeded},
        ).fetchone()
        succeeded_outbox = session.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback' "
                "AND payload->>'event_id' = :event_id"
            ),
            {"event_id": event_succeeded},
        ).fetchone()
    check(
        "D6",
        "SUCCEEDED 回调防重放记录已落库",
        succeeded_callback is not None,
        True,
        succeeded_callback is not None,
    )
    check(
        "D6",
        "SUCCEEDED 回调 outbox 已落库且记录 to_status=SUCCEEDED",
        succeeded_outbox is not None
        and succeeded_outbox[0].get("to_status") == "SUCCEEDED",
        {"exists": True, "to_status": "SUCCEEDED"},
        {
            "exists": succeeded_outbox is not None,
            "payload": succeeded_outbox[0] if succeeded_outbox else None,
        },
    )
'''
    text = replace_once(
        text, d6_succeeded_anchor, d6_succeeded_replacement, "D6 SUCCEEDED 四类落库"
    )

    # 9) D9：终态内容改写命中 terminal_immutable；状态回退命中转换守卫。
    d9_anchor = '''    direct_execute(
        "D9",
        "直接改写终态外部训练引用",
        "UPDATE external_training_references SET external_status = 'FAILED' WHERE id = %s",
        (UUID(external_ref_id),),
        "external_training_reference_terminal_immutable",
    )
'''
    d9_replacement = '''    direct_execute(
        "D9",
        "直接改写终态外部训练引用内容",
        "UPDATE external_training_references SET model_ref = %s::jsonb WHERE id = %s",
        ('{"model_id": "tampered"}', UUID(external_ref_id)),
        "external_training_reference_terminal_immutable",
    )
    direct_execute(
        "D9",
        "直接回退终态外部训练状态",
        "UPDATE external_training_references SET external_status = 'FAILED' WHERE id = %s",
        (UUID(external_ref_id),),
        "external_training_reference_state_transition_invalid",
    )
'''
    text = replace_once(text, d9_anchor, d9_replacement, "D9 终态守卫")

    # 10) main 文案与迁移跨度。
    text = text.replace("迁移 0011→0019 逐级升降级完成", "迁移 0011→0019→0020 逐级升降级完成")

    TARGET.write_text(text, encoding="utf-8")

    # 11) pytest 运行器同步 330 命名。
    pytest_text = PYTEST_TARGET.read_text(encoding="utf-8").replace("329", "330")
    PYTEST_TARGET.write_text(pytest_text, encoding="utf-8")
    print("DDP-330 探针适配完成")


if __name__ == "__main__":
    main()
