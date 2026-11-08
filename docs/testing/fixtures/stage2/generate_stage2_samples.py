#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDP 阶段 2 采集/上传/Episode 测试样例包生成器。

设计目标：
1. 只依赖 Python 标准库，不连接数据库、对象存储、网络或设备；
2. 使用固定种子、固定时间、固定排序与固定压缩参数，保证同一 Python 小版本
   下重复生成的文件字节一致；
3. 生成 `samples.manifest.json`（逐文件 SHA256、大小、用途、预期）与
   `SHA256SUMS`，并支持 `--check` 独立复核；
4. 明确标注 SYNTHETIC / SYNTHETIC_NEGATIVE / MOCK_SPEC，不冒充真实采集数据；
5. 超大文件默认不生成，使用 `--with-large` 按需生成，避免污染仓库。

边界：
- 本脚本只写 `docs/testing/fixtures/stage2/**`，不修改业务代码、配置、契约或数据库；
- 生成的 MP4/MCAP/HDF5/PARQUET 是“签名容器样例”，只用于上传、签名探测、
  错误路径与元数据测试，不可作为真实解码/解析验收；
- 恶意样本仅用于隔离环境测试，不得进入真实数据目录或生产桶。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any

FIXTURE_VERSION = "ddp.stage2.testfixtures/1.0.0"
FIXED_GENERATED_AT = "2026-11-08T00:00:00Z"
BASE_NS = 1_730_000_000_000_000_000  # 2024-10-27T00:00:00Z，固定基准
ROW_COUNT = 600  # 10 Hz × 60 s
ROW_INTERVAL_NS = 100_000_000

PROJECT_A = "018f2a3b-4c5d-7100-819b-0c1d2e3f0001"
PROJECT_B = "018f2a3b-4c5d-7200-829b-0c1d2e3f0002"
TASK_A = "018f2a3b-4c5d-7103-839b-0c1d2e3f0003"
BATCH_A = "018f2a3b-4c5d-7104-849b-0c1d2e3f0004"
ASSET_A = "018f2a3b-4c5d-7105-859b-0c1d2e3f0005"
ASSET_A_CAM_FRONT = "018f2a3b-4c5d-7106-869b-0c1d2e3f0006"
ASSET_A_CAM_WRIST = "018f2a3b-4c5d-7107-879b-0c1d2e3f0007"
ASSET_B = "018f2a3b-4c5d-7208-889b-0c1d2e3f0008"
EPISODE_A = "018f2a3b-4c5d-7109-899b-0c1d2e3f0009"
JOB_A = "018f2a3b-4c5d-710a-8a9b-0c1d2e3f000a"
SESSION_A = "018f2a3b-4c5d-710b-8b9b-0c1d2e3f000b"
SENSOR_FRONT = "018f2a3b-4c5d-710c-8c9b-0c1d2e3f000c"
SENSOR_WRIST = "018f2a3b-4c5d-710d-8d9b-0c1d2e3f000d"
SENSOR_STATE = "018f2a3b-4c5d-710e-8e9b-0c1d2e3f000e"
CALIBRATION_A = "018f2a3b-4c5d-710f-8f9b-0c1d2e3f000f"

FIXED_CHECKSUM = "sha256:" + "a" * 64
WRONG_CHECKSUM = "sha256:" + "0" * 64

REGISTRY: dict[str, dict[str, Any]] = {}


def sha256_bytes(data: bytes) -> str:
    """返回十六进制 SHA256。"""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA256，避免一次性读取大文件。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    """写入字节，确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_text(path: Path, text: str) -> None:
    """以固定 UTF-8 与 LF 换行写入文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def json_text(value: Any) -> str:
    """固定 JSON 序列化：排序键、两空格缩进、UTF-8、末尾换行。"""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, value: Any) -> None:
    write_text(path, json_text(value))


def record(
    relative_path: str,
    sample_id: str,
    purpose: str,
    expected: str,
    *,
    data_source: str = "SYNTHETIC",
    mock_scope: str = "SYNTHETIC",
    format_name: str = "JSON",
    reproducible: bool = True,
) -> None:
    """登记样例元数据，供 manifest 使用。"""
    REGISTRY[relative_path] = {
        "sample_id": sample_id,
        "purpose": purpose,
        "expected": expected,
        "data_source": data_source,
        "mock_scope": mock_scope,
        "format": format_name,
        "reproducible": reproducible,
        "on_demand": False,
    }


def mp4_box(kind: bytes, payload: bytes) -> bytes:
    """构造 MP4 box；仅用于签名容器样例。"""
    return struct.pack(">I", len(payload) + 8) + kind + payload


def build_mp4(seed: int) -> bytes:
    """构造带 ftyp 签名的确定性 MP4 容器样例，不包含真实编码帧。"""
    ftyp = mp4_box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2avc1mp41")
    moov = mp4_box(b"moov", b"")
    payload = bytearray()
    for index in range(ROW_COUNT):
        payload.extend(struct.pack(">I", index))
        payload.extend(bytes([(seed + index) % 256]) * 32)
    mdat = mp4_box(b"mdat", bytes(payload))
    return ftyp + moov + mdat


def build_mcap() -> bytes:
    """构造 MCAP 魔数容器样例，不包含真实记录。"""
    return b"\x89MCAP0\r\n" + b"SYNTHETIC_MCAP_CONTAINER_ONLY\n" + bytes(range(64))


def build_hdf5() -> bytes:
    """构造 HDF5 签名容器样例，不包含真实数据集。"""
    return b"\x89HDF\r\n\x1a\n" + b"SYNTHETIC_HDF5_CONTAINER_ONLY\n" + bytes(range(128))


def build_parquet() -> bytes:
    """构造首尾 PAR1 的容器样例，不包含真实列式数据。"""
    body = b"SYNTHETIC_PARQUET_CONTAINER_ONLY\n" + bytes(range(64))
    return b"PAR1" + body + b"PAR1"


def build_rosbag2() -> bytes:
    """构造 SQLite 文件头样例，不包含真实 ROSBAG2 表结构。"""
    header = b"SQLite format 3\x00" + struct.pack(">H", 4096) + b"\x00\x00"
    return header + b"SYNTHETIC_ROSBAG2_CONTAINER_ONLY\n" + bytes(range(128))


def robot_state_csv(
    *,
    offset_ns: int = 0,
    missing_gripper: bool = False,
    duplicate_timestamp: bool = False,
    nonmonotonic: bool = False,
) -> str:
    """生成确定性机器人状态 CSV，默认 10 Hz × 60 s。"""
    headers = [
        "timestamp_ns",
        "sequence",
        "joint_1",
        "joint_2",
        "joint_3",
        "joint_4",
        "joint_5",
        "joint_6",
        "ee_x",
        "ee_y",
        "ee_z",
        "ee_qw",
        "ee_qx",
        "ee_qy",
        "ee_qz",
    ]
    if not missing_gripper:
        headers.append("gripper")

    lines = [",".join(headers)]
    for index in range(ROW_COUNT):
        timestamp = BASE_NS + index * ROW_INTERVAL_NS + offset_ns
        if duplicate_timestamp and index == 300:
            timestamp = BASE_NS + 299 * ROW_INTERVAL_NS + offset_ns
        if nonmonotonic and index == 300:
            timestamp = BASE_NS + 100 * ROW_INTERVAL_NS + offset_ns

        joints = [((index * (joint + 3)) % 1000) / 1000.0 for joint in range(1, 7)]
        ee = [
            ((index * 7) % 1000) / 1000.0,
            ((index * 11) % 1000) / 1000.0,
            ((index * 13) % 1000) / 1000.0,
        ]
        values = [
            str(timestamp),
            str(index),
            *[f"{value:.6f}" for value in joints],
            *[f"{value:.6f}" for value in ee],
            "1.000000",
            "0.000000",
            "0.000000",
            "0.000000",
        ]
        if not missing_gripper:
            values.append("1.000000" if index % 30 == 0 else "0.000000")
        lines.append(",".join(values))
    return "\n".join(lines) + "\n"


def time_base(sync_status: str = "VERIFIED") -> dict[str, Any]:
    """构造 Canonical 时间基准。"""
    return {
        "time_domain": "UTC",
        "clock_source": "PTP",
        "clock_id": "host-stage2-fixture-01",
        "offset_to_utc_ns": "0",
        "drift_ppm": 0.0,
        "uncertainty_ns": "1000000",
        "sync_method": "PTP",
        "sync_ref": "ptp-master-fixture-01",
        "sync_status": sync_status,
    }


def build_samples(samples_root: Path) -> None:
    """生成全部固定样例。"""
    write_text(
        samples_root / "README.md",
        "# DDP 阶段 2 合成样例包\n\n"
        "本目录由 `generate_stage2_samples.py` 生成，仅供测试使用。\n\n"
        "- 数据来源：`[SYNTHETIC]`，不是真实采集数据；\n"
        "- MP4/MCAP/HDF5/PARQUET 为签名容器样例，不可用于真实解码/解析验收；\n"
        "- 负向样本只允许在隔离环境使用，不得进入真实数据目录或生产桶；\n"
        "- 逐文件 SHA256 见 `../samples.manifest.json` 与 `../SHA256SUMS`；\n"
        "- 复现命令：`backend/.venv/bin/python docs/testing/fixtures/stage2/generate_stage2_samples.py`。\n",
    )
    record(
        "README.md",
        "S2-SYN-README",
        "样例包使用边界与复现说明",
        "阅读后不得把容器样例当作真实媒体或真实采集数据",
        format_name="MARKDOWN",
    )

    # 相机：签名容器样例
    front_bytes = build_mp4(seed=11)
    wrist_bytes = build_mp4(seed=29)
    write_bytes(samples_root / "cameras/cam_front.mp4", front_bytes)
    write_bytes(samples_root / "cameras/cam_wrist.mp4", wrist_bytes)
    record(
        "cameras/cam_front.mp4",
        "S2-SYN-CAM-FRONT",
        "正面相机 MP4 签名容器样例",
        "上传/签名探测成功；不得用于真实 H.264 解码验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="MP4_SYNTHETIC_CONTAINER_ONLY",
    )
    record(
        "cameras/cam_wrist.mp4",
        "S2-SYN-CAM-WRIST",
        "腕部相机 MP4 签名容器样例",
        "上传/签名探测成功；不得用于真实 H.264 解码验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="MP4_SYNTHETIC_CONTAINER_ONLY",
    )

    # 机器人状态与变体
    state_text = robot_state_csv()
    write_text(samples_root / "robot/robot_state.csv", state_text)
    record(
        "robot/robot_state.csv",
        "S2-SYN-ROBOT-STATE",
        "机器人状态 10 Hz × 60 s，含关节、末端位姿、夹爪",
        "字段/采样率/时间范围校验通过",
        format_name="CSV",
    )

    write_text(
        samples_root / "robot/robot_state_offset_plus_2s.csv",
        robot_state_csv(offset_ns=2_000_000_000),
    )
    record(
        "robot/robot_state_offset_plus_2s.csv",
        "S2-SYN-ROBOT-STATE-OFFSET-2S",
        "机器人状态整体 +2 s 偏移",
        "超过项目冻结阈值时阻断/隔离；阈值未冻结前不得断言具体结果",
        format_name="CSV",
    )

    write_text(
        samples_root / "robot/robot_state_missing_gripper.csv",
        robot_state_csv(missing_gripper=True),
    )
    record(
        "robot/robot_state_missing_gripper.csv",
        "S2-SYN-ROBOT-STATE-MISSING-GRIPPER",
        "缺少夹爪通道",
        "完整性检查失败并进入隔离/返工；不得默认成功",
        format_name="CSV",
    )

    write_text(
        samples_root / "robot/robot_state_duplicate_ts.csv",
        robot_state_csv(duplicate_timestamp=True),
    )
    record(
        "robot/robot_state_duplicate_ts.csv",
        "S2-SYN-ROBOT-STATE-DUP-TS",
        "存在重复时间戳",
        "标记 DUPLICATE 并进入质检；不得静默去重",
        format_name="CSV",
    )

    write_text(
        samples_root / "robot/robot_state_nonmonotonic.csv",
        robot_state_csv(nonmonotonic=True),
    )
    record(
        "robot/robot_state_nonmonotonic.csv",
        "S2-SYN-ROBOT-STATE-NONMONOTONIC",
        "存在时间倒退",
        "对齐后流内时间必须非递减；失败进入质检",
        format_name="CSV",
    )

    # 重复内容：不同文件名、相同字节
    write_text(samples_root / "duplicates/robot_state_copy.csv", state_text)
    record(
        "duplicates/robot_state_copy.csv",
        "S2-SYN-DUP-CONTENT",
        "与 robot/robot_state.csv 字节完全相同、文件名不同",
        "同项目重复上传去重；跨项目不得去重",
        format_name="CSV",
    )

    # 其他格式签名容器
    write_bytes(samples_root / "formats/sample.mcap", build_mcap())
    write_bytes(samples_root / "formats/sample.db3", build_rosbag2())
    write_bytes(samples_root / "formats/sample.h5", build_hdf5())
    write_bytes(samples_root / "formats/sample.parquet", build_parquet())
    record(
        "formats/sample.mcap",
        "S2-SYN-MCAP",
        "MCAP 魔数容器样例",
        "签名探测通过；不可作为真实 MCAP 解析验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="MCAP_SYNTHETIC_CONTAINER_ONLY",
    )
    record(
        "formats/sample.db3",
        "S2-SYN-ROSBAG2",
        "ROSBAG2/SQLite 文件头样例",
        "签名探测通过；不可作为真实 ROSBAG2 解析验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="ROSBAG2_SYNTHETIC_CONTAINER_ONLY",
    )
    record(
        "formats/sample.h5",
        "S2-SYN-HDF5",
        "HDF5 签名容器样例",
        "签名探测通过；不可作为真实 HDF5 解析验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="HDF5_SYNTHETIC_CONTAINER_ONLY",
    )
    record(
        "formats/sample.parquet",
        "S2-SYN-PARQUET",
        "Parquet 首尾 PAR1 容器样例",
        "签名探测通过；不可作为真实 Parquet 解析验收",
        mock_scope="SYNTHETIC_CONTAINER_ONLY",
        format_name="PARQUET_SYNTHETIC_CONTAINER_ONLY",
    )

    # 元数据
    write_json(
        samples_root / "metadata/streams.json",
        {
            "fixture_version": FIXTURE_VERSION,
            "data_source": "SYNTHETIC",
            "project_id": PROJECT_A,
            "task_id": TASK_A,
            "batch_id": BATCH_A,
            "asset_ids": [ASSET_A_CAM_FRONT, ASSET_A_CAM_WRIST],
            "time_base": time_base(),
            "start_time_ns": str(BASE_NS),
            "end_time_ns": str(BASE_NS + ROW_COUNT * ROW_INTERVAL_NS),
            "streams": [
                {
                    "stream_id": "cam_front",
                    "sensor_id": SENSOR_FRONT,
                    "modality": "RGB",
                    "path": "cameras/cam_front.mp4",
                    "container": "MP4_SYNTHETIC_CONTAINER_ONLY",
                    "declared_frame_count": ROW_COUNT,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
                {
                    "stream_id": "cam_wrist",
                    "sensor_id": SENSOR_WRIST,
                    "modality": "RGB",
                    "path": "cameras/cam_wrist.mp4",
                    "container": "MP4_SYNTHETIC_CONTAINER_ONLY",
                    "declared_frame_count": ROW_COUNT,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
                {
                    "stream_id": "robot_state",
                    "sensor_id": SENSOR_STATE,
                    "modality": "PROPRIOCEPTION",
                    "path": "robot/robot_state.csv",
                    "container": "CSV",
                    "declared_frame_count": ROW_COUNT,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
            ],
            "limitations": [
                "MP4/MCAP/HDF5/PARQUET 为签名容器样例，不包含真实编码帧或数据集",
                "真实解码/解析验收需要 T1 脱敏真实样例或真实设备数据",
                "时间同步阈值需由项目配置冻结后批准，本样例只提供偏移梯度",
            ],
        },
    )
    record(
        "metadata/streams.json",
        "S2-SYN-STREAMS-META",
        "多相机 + 机器人状态流元数据",
        "流数量、模态、采样率、时间范围可被校验",
    )

    write_json(
        samples_root / "metadata/language_instructions.json",
        {
            "data_source": "SYNTHETIC",
            "instructions": [
                {
                    "instruction_id": "instr-001",
                    "language": "zh-CN",
                    "text": "请将红色方块放入左侧托盘。",
                    "start_time_ns": str(BASE_NS),
                    "end_time_ns": str(BASE_NS + 5_000_000_000),
                },
                {
                    "instruction_id": "instr-002",
                    "language": "en-US",
                    "text": "Pick up the blue cube and place it on the right tray.",
                    "start_time_ns": str(BASE_NS + 5_000_000_000),
                    "end_time_ns": str(BASE_NS + 10_000_000_000),
                },
            ],
        },
    )
    record(
        "metadata/language_instructions.json",
        "S2-SYN-LANGUAGE",
        "动作/语言指令元数据",
        "语言指令不可替代审核标签",
    )

    valid_calibration = {
        "calibration_id": CALIBRATION_A,
        "calibration_version": 3,
        "checksum": FIXED_CHECKSUM,
        "target_type": "SENSOR",
        "target_id": SENSOR_FRONT,
        "schema_version": "ddp.calibration/1.0.0",
        "status": "ACTIVE",
        "valid_from": "2026-11-07T00:00:00Z",
        "valid_to": "2026-11-09T00:00:00Z",
        "captured_at": "2026-11-08T00:00:00Z",
        "intrinsics": {"fx": 615.0, "fy": 615.0, "cx": 320.0, "cy": 240.0},
        "extrinsics": {"parent_frame": "robot/base_link", "child_frame": "sensor/cam_front_optical"},
    }
    write_json(samples_root / "metadata/calibration_snapshot.json", valid_calibration)
    record(
        "metadata/calibration_snapshot.json",
        "S2-SYN-CALIBRATION-VALID",
        "有效 ACTIVE 标定快照",
        "满足 valid_from <= captured_at < valid_to；Episode 创建成功",
    )

    expired_calibration = dict(valid_calibration)
    expired_calibration.update(
        {
            "calibration_id": "018f2a3b-4c5d-7110-809b-0c1d2e3f0010",
            "calibration_version": 2,
            "valid_from": "2026-11-06T00:00:00Z",
            "valid_to": "2026-11-07T12:00:00Z",
        }
    )
    write_json(samples_root / "metadata/calibration_expired.json", expired_calibration)
    record(
        "metadata/calibration_expired.json",
        "S2-SYN-CALIBRATION-EXPIRED",
        "已过期标定快照",
        "Episode/开工检查阻断，返回 calibration_not_active",
    )

    revoked_calibration = dict(valid_calibration)
    revoked_calibration.update(
        {
            "calibration_id": "018f2a3b-4c5d-7111-819b-0c1d2e3f0011",
            "calibration_version": 4,
            "status": "REVOKED",
        }
    )
    write_json(samples_root / "metadata/calibration_revoked.json", revoked_calibration)
    record(
        "metadata/calibration_revoked.json",
        "S2-SYN-CALIBRATION-REVOKED",
        "已撤销标定快照",
        "引用该快照的 Episode 进入复查/INVALIDATED",
    )

    write_json(
        samples_root / "metadata/episode_manifest_synthetic.json",
        {
            "schema_version": "ddp.canonical/1.0.0-draft",
            "episode_id": EPISODE_A,
            "episode_lineage_id": "018f2a3b-4c5d-7112-829b-0c1d2e3f0012",
            "episode_version": 1,
            "project_id": PROJECT_A,
            "code": "ep-synthetic-001",
            "task_id": TASK_A,
            "batch_id": BATCH_A,
            "start_time_ns": str(BASE_NS),
            "end_time_ns": str(BASE_NS + 10_000_000_000),
            "duration_ns": str(10_000_000_000),
            "time_base": time_base(),
            "time_alignment": {
                "reference_stream_id": "cam_front",
                "method": "PTP",
                "offset_ns": "0",
                "uncertainty_ns": "1000000",
                "residual_ns": "100000",
                "sample_count": 600,
            },
            "calibration_snapshot": [valid_calibration],
            "streams": [
                {
                    "stream_id": "cam_front",
                    "sensor_id": SENSOR_FRONT,
                    "modality": "RGB",
                    "frame_count": 100,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
                {
                    "stream_id": "cam_wrist",
                    "sensor_id": SENSOR_WRIST,
                    "modality": "RGB",
                    "frame_count": 100,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
                {
                    "stream_id": "robot_state",
                    "sensor_id": SENSOR_STATE,
                    "modality": "PROPRIOCEPTION",
                    "frame_count": 100,
                    "sample_rate_hz": 10,
                    "dropped_count": 0,
                },
            ],
            "raw_asset_ids": [ASSET_A_CAM_FRONT, ASSET_A_CAM_WRIST],
            "provenance": {
                "ingestion_job_ids": [JOB_A],
                "pipeline_id": "ingest-pipeline",
                "pipeline_version": "1.0.0",
                "parser_id": "synthetic-parser",
                "parser_version": "1.0.0",
                "config_hash": FIXED_CHECKSUM,
                "segmentation_rule_version": "seg-manual-v1",
            },
            "quality": {
                "status": "NOT_CHECKED",
                "rule_version": "quality-rules-v1",
                "issues": [],
            },
            "manifest_sha256": None,
            "note": "manifest_sha256 由服务端对规范化 JSON 计算；样例不预置，避免被误当作已验证 Hash。",
        },
    )
    record(
        "metadata/episode_manifest_synthetic.json",
        "S2-SYN-EPISODE-MANIFEST",
        "Episode Manifest 合法结构样例",
        "Schema 校验通过；服务端重新计算 manifest_sha256",
    )

    write_json(
        samples_root / "metadata/episode_boundary_cases.json",
        [
            {"id": "EP-MIN", "start_ns": "0", "end_ns": "1", "expected": "ALLOW_IF_WITHIN_ASSET", "note": "最短非零区间"},
            {"id": "EP-EXACT", "start_ns": str(BASE_NS), "end_ns": str(BASE_NS + 60_000_000_000), "expected": "ALLOW", "note": "正好等于资产范围"},
            {"id": "EP-ZERO", "start_ns": str(BASE_NS), "end_ns": str(BASE_NS), "expected": "422 episode_time_range_invalid", "note": "start=end"},
            {"id": "EP-REVERSED", "start_ns": str(BASE_NS + 1_000_000_000), "end_ns": str(BASE_NS), "expected": "422 episode_time_range_invalid", "note": "end<start"},
            {"id": "EP-OUTSIDE-BEFORE", "start_ns": str(BASE_NS - 1), "end_ns": str(BASE_NS + 1_000_000_000), "expected": "422 episode_time_range_invalid", "note": "早于资产范围"},
            {"id": "EP-OUTSIDE-AFTER", "start_ns": str(BASE_NS + 59_000_000_000), "end_ns": str(BASE_NS + 61_000_000_000), "expected": "422 episode_time_range_invalid", "note": "晚于资产范围"},
            {"id": "EP-GAP", "start_ns": str(BASE_NS), "end_ns": str(BASE_NS + 120_000_000_000), "expected": "422 episode_time_range_invalid", "note": "跨资产缺失时间"},
            {"id": "EP-OVERLAP-DENY", "start_ns": str(BASE_NS + 1_000_000_000), "end_ns": str(BASE_NS + 5_000_000_000), "expected": "409 episode_overlap_conflict", "note": "与既有 Episode 重叠且 allow_overlap=false"},
            {"id": "EP-OVERLAP-ALLOW", "start_ns": str(BASE_NS + 1_000_000_000), "end_ns": str(BASE_NS + 5_000_000_000), "expected": "201 + audit", "note": "DE/QI allow_overlap=true 且填写原因"},
            {"id": "EP-ASSETS-64", "asset_count": 64, "expected": "ALLOW", "note": "契约上限"},
            {"id": "EP-ASSETS-65", "asset_count": 65, "expected": "422 validation_failed", "note": "超过上限"},
            {"id": "EP-RULE-REPLAY", "expected": "IDENTICAL_BOUNDARIES", "note": "同输入+规则版本+配置 Hash 必须得到相同边界"},
            {"id": "EP-READY-IMMUTABLE", "expected": "409 resource_immutable", "note": "READY 后 PATCH 数据字段"},
            {"id": "EP-REVISION", "expected": "NEW_EPISODE_ID_VERSION_PLUS_1", "note": "旧版本不变，supersedes_id 指向旧版"},
            {"id": "EP-INVALIDATE", "expected": "INVALIDATED + lineage edge", "note": "墓碑/隔离/标定撤销传播"},
        ],
    )
    record(
        "metadata/episode_boundary_cases.json",
        "S2-SYN-EPISODE-BOUNDARY-CASES",
        "Episode 边界与不可变/修订用例参数",
        "逐条被自动化用例消费；不得只做人工核对",
        data_source="SYNTHETIC_SPEC",
        mock_scope="SYNTHETIC_SPEC",
    )

    write_json(
        samples_root / "metadata/time_alignment_cases.json",
        [
            {"id": "TS-VERIFIED-0", "offset_ns": "0", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "PASS"},
            {"id": "TS-OFFSET-100US", "offset_ns": "100000", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "PASS_OR_WARN_BY_PROJECT_THRESHOLD"},
            {"id": "TS-OFFSET-10MS", "offset_ns": "10000000", "drift_ppm": 0.0, "sync_status": "DEGRADED", "expected": "QUALITY_WARN"},
            {"id": "TS-OFFSET-2S", "offset_ns": "2000000000", "drift_ppm": 0.0, "sync_status": "FAILED", "expected": "BLOCK_EPISODE"},
            {"id": "TS-UNVERIFIED", "offset_ns": None, "drift_ppm": None, "sync_status": "UNVERIFIED", "expected": "BLOCK_TRAINING_DATASET"},
            {"id": "TS-FAILED", "offset_ns": None, "drift_ppm": None, "sync_status": "FAILED", "expected": "BLOCK_EPISODE"},
            {"id": "TS-NONMONOTONIC", "offset_ns": "0", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "QUALITY_FAIL"},
            {"id": "TS-DUPLICATE", "offset_ns": "0", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "QUALITY_FAIL"},
            {"id": "TS-SAMPLE-RATE-5PCT", "offset_ns": "0", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "WARN_AT_DEFAULT_THRESHOLD"},
            {"id": "TS-SAMPLE-RATE-6PCT", "offset_ns": "0", "drift_ppm": 0.0, "sync_status": "VERIFIED", "expected": "WARN_OR_FAIL_BY_PROJECT_THRESHOLD"},
            {"id": "TS-THRESHOLD-PENDING", "offset_ns": None, "drift_ppm": None, "sync_status": "UNVERIFIED", "expected": "BLOCKED_UNTIL_PROJECT_THRESHOLD_APPROVED"},
        ],
    )
    record(
        "metadata/time_alignment_cases.json",
        "S2-SYN-TIME-ALIGNMENT-CASES",
        "时间同步偏移/状态/丢帧/重复/采样率用例参数",
        "阈值未冻结前只能验证失败关闭与状态传播，不得断言具体阈值",
        data_source="SYNTHETIC_SPEC",
        mock_scope="SYNTHETIC_SPEC",
    )

    write_json(
        samples_root / "metadata/network_profiles.json",
        [
            {"id": "NET-DELAY-100MS", "tool": "tc-netem", "args": "delay 100ms", "duration_sec": 120, "expected": "上传变慢但最终 Hash 一致"},
            {"id": "NET-DELAY-1000MS", "tool": "tc-netem", "args": "delay 1000ms", "duration_sec": 120, "expected": "不超时；进度可见；最终 Hash 一致"},
            {"id": "NET-LOSS-5", "tool": "tc-netem", "args": "loss 5%", "duration_sec": 120, "expected": "失败分片重试；无重复资产"},
            {"id": "NET-LOSS-20", "tool": "tc-netem", "args": "loss 20%", "duration_sec": 120, "expected": "退避有上限；最终可恢复或明确失败"},
            {"id": "NET-BW-1M", "tool": "tc-netem", "args": "rate 1mbit", "duration_sec": 300, "expected": "不伪造进度；超时策略生效"},
            {"id": "NET-DISCONNECT-30S", "tool": "iptables/tc", "args": "drop all for 30s", "duration_sec": 30, "expected": "断点续传；已确认分片不重传"},
            {"id": "NET-CORRUPT-1", "tool": "tc-netem", "args": "corrupt 1%", "duration_sec": 120, "expected": "Hash/校验失败被发现；不得登记可用资产"},
            {"id": "NET-DUPLICATE-1", "tool": "tc-netem", "args": "duplicate 1%", "duration_sec": 120, "expected": "分片重复 ack 幂等；无重复资产"},
            {"id": "NET-REORDER-1", "tool": "tc-netem", "args": "reorder 1% 50%", "duration_sec": 120, "expected": "乱序分片可完成；无损坏"},
        ],
    )
    record(
        "metadata/network_profiles.json",
        "S2-SYN-NETWORK-PROFILES",
        "弱网/断网注入参数模板",
        "只用于 MOCK 网络注入；离线本地队列方案未验证时相关 AC 仍 BLOCKED",
        data_source="MOCK_SPEC",
        mock_scope="MOCK_NETWORK_INJECTION",
    )

    write_json(
        samples_root / "metadata/upload_scenarios.json",
        [
            {"id": "UPL-COMPLETE", "steps": ["part 1", "part 2", "part 3", "complete"], "expected": "202 + RawAsset VALIDATING"},
            {"id": "UPL-MISSING-PART", "steps": ["part 1", "part 3", "complete"], "expected": "409 upload_incomplete; details [2]"},
            {"id": "UPL-DUP-PART-SAME", "steps": ["ack 1", "ack 1 same etag/size/hash"], "expected": "idempotent; one record"},
            {"id": "UPL-DUP-PART-DIFF", "steps": ["ack 1", "ack 1 different hash"], "expected": "409 upload_part_conflict"},
            {"id": "UPL-SIZE-MISMATCH", "steps": ["complete with wrong size"], "expected": "422 size_mismatch; session correctable"},
            {"id": "UPL-SIGNATURE-MISMATCH", "steps": ["declare MP4", "upload text"], "expected": "422 file_signature_mismatch; no usable asset"},
            {"id": "UPL-HASH-MISMATCH", "steps": ["complete", "async validate fails"], "expected": "RawAsset REJECTED; hash_mismatch"},
            {"id": "UPL-DEDUP-SAME-PROJECT", "steps": ["complete twice same content"], "expected": "deduplicated=true; one usable asset"},
            {"id": "UPL-NO-DEDUP-CROSS-PROJECT", "steps": ["complete same content in A and B"], "expected": "two assets; no cross-project dedup"},
            {"id": "UPL-CONCURRENT-COMPLETE", "steps": ["two complete concurrently"], "expected": "one succeeds; one idempotent or 409; no duplicate usable asset"},
            {"id": "UPL-EXPIRED", "steps": ["wait expires_at", "request part url"], "expected": "410 upload_session_expired"},
            {"id": "UPL-ABORT-IDEMPOTENT", "steps": ["abort", "abort again"], "expected": "current state; no repeated delete"},
            {"id": "UPL-CROSS-PROJECT", "steps": ["A user accesses B session"], "expected": "404; audit"},
            {"id": "UPL-QUARANTINE-RELEASE-SELF", "steps": ["same user propose and approve"], "expected": "403 forbidden"},
        ],
    )
    record(
        "metadata/upload_scenarios.json",
        "S2-SYN-UPLOAD-SCENARIOS",
        "上传分片/幂等/去重/并发/过期/跨项目用例参数",
        "逐条自动化；不得只测 happy path",
        data_source="SYNTHETIC_SPEC",
        mock_scope="SYNTHETIC_SPEC",
    )

    write_json(
        samples_root / "metadata/filename_cases.json",
        [
            {"id": "FN-PATH-TRAVERSAL", "filename": "../../etc/passwd", "expected": "422 filename_invalid 或安全清洗；对象 Key 不含该名"},
            {"id": "FN-URL-ENCODED", "filename": "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "expected": "拒绝或安全清洗"},
            {"id": "FN-BACKSLASH", "filename": "..\\..\\windows\\system32\\config", "expected": "拒绝或安全清洗"},
            {"id": "FN-NUL", "filename": "video.mp4\\u0000.exe", "expected": "拒绝；不得截断绕过"},
            {"id": "FN-DOUBLE-EXT", "filename": "video.mp4.exe", "expected": "按内容签名判定；扩展名不作为放行依据"},
            {"id": "FN-LONG", "filename": "a" * 300 + ".mp4", "expected": "422 filename_invalid"},
            {"id": "FN-CONTROL", "filename": "video\\u0001.mp4", "expected": "拒绝或清洗"},
            {"id": "FN-HIDDEN", "filename": ".env", "expected": "415/422；不得登记"},
        ],
    )
    record(
        "metadata/filename_cases.json",
        "S2-SYN-FILENAME-CASES",
        "恶意/非法文件名用例参数",
        "只作为 API 字符串输入，不要求在磁盘创建同名文件",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
    )

    write_json(
        samples_root / "metadata/cross_project_cases.json",
        [
            {"id": "XP-LIST", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "列表不泄漏"},
            {"id": "XP-DETAIL", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "404 resource_not_found"},
            {"id": "XP-DOWNLOAD", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "404；审计跨项目拒绝"},
            {"id": "XP-DEDUP", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "同内容不跨项目去重；两个资产"},
            {"id": "XP-LINEAGE", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "跨项目血缘边被 DB 触发器拒绝"},
            {"id": "XP-EPISODE-ASSET", "actor_project": PROJECT_A, "target_project": PROJECT_B, "expected": "422/404；不得引用跨项目资产"},
        ],
    )
    record(
        "metadata/cross_project_cases.json",
        "S2-SYN-CROSS-PROJECT-CASES",
        "跨项目隔离用例参数",
        "所有用例必须使用真实实现；不得用 Mock 替代",
        data_source="SYNTHETIC_SPEC",
        mock_scope="SYNTHETIC_SPEC",
    )

    # 跨项目资产元数据：与 robot_state.csv 同内容，验证不去重
    state_hash = sha256_bytes(state_text.encode("utf-8"))
    write_json(
        samples_root / "cross_project/project_b_asset_meta.json",
        {
            "data_source": "SYNTHETIC",
            "project_id": PROJECT_B,
            "asset_id": ASSET_B,
            "content_source": "../robot/robot_state.csv",
            "sha256": "sha256:" + state_hash,
            "size_bytes": len(state_text.encode("utf-8")),
            "expected": "与项目 A 同内容但不得跨项目去重或互相可见",
        },
    )
    record(
        "cross_project/project_b_asset_meta.json",
        "S2-SYN-CROSS-PROJECT-ASSET",
        "项目 B 同内容资产元数据",
        "项目 A/B 各自保留资产；跨项目不可见、不去重",
    )

    # 负向样本
    write_bytes(samples_root / "negative/empty.mp4", b"")
    record(
        "negative/empty.mp4",
        "S2-NEG-EMPTY",
        "空文件",
        "422/413；不得登记可用资产",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="EMPTY",
    )

    write_bytes(samples_root / "negative/truncated_cam_front.mp4", front_bytes[:24])
    record(
        "negative/truncated_cam_front.mp4",
        "S2-NEG-TRUNCATED",
        "截断的 MP4 容器",
        "解析/校验失败；不得登记可用资产",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="TRUNCATED_MP4",
    )

    write_bytes(
        samples_root / "negative/text_as_mp4.mp4",
        b"This is a text file disguised as MP4.\nNot a real container.\n",
    )
    record(
        "negative/text_as_mp4.mp4",
        "S2-NEG-TEXT-AS-MP4",
        "扩展名 MP4、内容为文本",
        "422 file_signature_mismatch；不得按扩展名放行",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="TEXT_AS_MP4",
    )

    write_bytes(
        samples_root / "negative/unknown.bin",
        b"\x7fELF\x02\x01\x01\x00" + b"SYNTHETIC_UNKNOWN_FORMAT\n" + bytes(range(64)),
    )
    record(
        "negative/unknown.bin",
        "S2-NEG-UNKNOWN",
        "白名单外/未知格式",
        "415 file_type_unsupported 或隔离 unsupported_format",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="UNKNOWN_BINARY",
    )

    write_text(
        samples_root / "negative/corrupt_manifest.json",
        '{"schema_version": "ddp.canonical/1.0.0-draft", "streams": ['
        '{"stream_id": "cam_front", "frame_count": }]}\n',
    )
    record(
        "negative/corrupt_manifest.json",
        "S2-NEG-CORRUPT-MANIFEST",
        "非法 JSON Manifest",
        "422 manifest_schema_invalid；不得生成 READY Episode",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="INVALID_JSON",
    )

    write_text(
        samples_root / "negative/manifest_hash_mismatch.json",
        json_text(
            {
                "schema_version": "ddp.canonical/1.0.0-draft",
                "episode_id": EPISODE_A,
                "manifest_sha256": WRONG_CHECKSUM,
                "note": "故意错误的 manifest_sha256，用于服务端重新计算后比对失败",
            }
        ),
    )
    record(
        "negative/manifest_hash_mismatch.json",
        "S2-NEG-MANIFEST-HASH",
        "Manifest Hash 不匹配",
        "422 manifest_hash_mismatch；Episode FAILED/INVALIDATED",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="JSON",
    )

    write_text(
        samples_root / "negative/malformed_state.csv",
        "timestamp_ns,sequence,joint_1,joint_2\n"
        "not-a-number,0,0.1,0.2\n"
        f"{BASE_NS + ROW_INTERVAL_NS},1,NaN,0.3\n"
        f"{BASE_NS + 2 * ROW_INTERVAL_NS},2,0.4\n",
    )
    record(
        "negative/malformed_state.csv",
        "S2-NEG-MALFORMED-CSV",
        "缺列/非数值/NaN 的状态 CSV",
        "完整性/数值校验失败；进入隔离或返工",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="MALFORMED_CSV",
    )

    def build_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload in entries:
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o644 << 16
                archive.writestr(info, payload)

    build_zip(
        samples_root / "negative/zip_path_traversal.zip",
        [("../escape.txt", b"path traversal probe\n"), ("safe/keep.txt", b"safe\n")],
    )
    record(
        "negative/zip_path_traversal.zip",
        "S2-NEG-ZIP-TRAVERSAL",
        "包含 ../ 路径穿越条目的 ZIP",
        "解压前拒绝或安全重命名；不得写出目标目录",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="ZIP",
    )

    inner_zip_path = samples_root / "negative/_inner_fixture.zip"
    build_zip(inner_zip_path, [("inner.txt", b"nested zip probe\n")])
    inner_bytes = inner_zip_path.read_bytes()
    inner_zip_path.unlink()
    build_zip(
        samples_root / "negative/zip_nested.zip",
        [("nested/inner.zip", inner_bytes)],
    )
    record(
        "negative/zip_nested.zip",
        "S2-NEG-ZIP-NESTED",
        "嵌套 ZIP（非压缩炸弹）",
        "嵌套深度检查生效；超限拒绝或隔离",
        data_source="SYNTHETIC_NEGATIVE",
        mock_scope="SYNTHETIC_NEGATIVE",
        format_name="ZIP",
    )


def collect_samples(
    samples_root: Path,
    metadata_by_path: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """收集并计算样例文件的哈希与元数据。

    `metadata_by_path` 用于 `--check`：从既有 manifest 读取元数据，避免复核进程
    因 REGISTRY 为空而把已登记样例误判为“未登记”。
    """
    items: list[dict[str, Any]] = []
    for path in sorted(samples_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(samples_root).as_posix()
        if relative in {"samples.manifest.json", "SHA256SUMS"}:
            continue
        if relative.startswith("large/"):
            # 超大文件为按需生成，不进入固定清单。
            continue
        if metadata_by_path is not None and relative in metadata_by_path:
            meta = {
                key: value
                for key, value in metadata_by_path[relative].items()
                if key not in {"path", "sha256", "size_bytes"}
            }
        else:
            meta = REGISTRY.get(relative)
        if meta is None:
            meta = {
                "sample_id": "S2-FIXTURE-" + sha256_bytes(relative.encode("utf-8"))[:8],
                "purpose": "未登记样例",
                "expected": "需补充登记",
                "data_source": "SYNTHETIC",
                "mock_scope": "SYNTHETIC",
                "format": "UNKNOWN",
                "reproducible": True,
                "on_demand": False,
            }
        item = dict(meta)
        item["path"] = relative
        data = path.read_bytes()
        item["sha256"] = "sha256:" + sha256_bytes(data)
        item["size_bytes"] = len(data)
        items.append(item)
    return items


def build_manifest(
    samples_root: Path,
    metadata_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造确定性 manifest。"""
    items = collect_samples(samples_root, metadata_by_path)
    counts: dict[str, int] = {}
    total_bytes = 0
    for item in items:
        counts[item["data_source"]] = counts.get(item["data_source"], 0) + 1
        total_bytes += int(item["size_bytes"])
    return {
        "manifest_version": FIXTURE_VERSION,
        "fixture_set": "stage2-collection-ingestion",
        "task": "DDP-306",
        "data_source": "SYNTHETIC",
        "generated_at_utc": FIXED_GENERATED_AT,
        "generator": "docs/testing/fixtures/stage2/generate_stage2_samples.py",
        "reproducibility": {
            "seed": "fixed-constants",
            "time_base_ns": str(BASE_NS),
            "json": "sort_keys=True, indent=2, ensure_ascii=False, UTF-8, LF",
            "csv": "UTF-8, LF, fixed formatting",
            "zip": "ZIP_STORED, fixed date_time=(2026,1,1,0,0,0)",
            "manifest_excludes": ["samples.manifest.json", "SHA256SUMS", "large/**"],
        },
        "counts": {
            "total_files": len(items),
            "total_bytes": total_bytes,
            "by_data_source": dict(sorted(counts.items())),
        },
        "samples": items,
    }


def write_manifest_and_sums(samples_root: Path, fixture_root: Path) -> dict[str, Any]:
    """写入 manifest 与 SHA256SUMS。"""
    manifest = build_manifest(samples_root)
    write_json(fixture_root / "samples.manifest.json", manifest)
    lines = [
        f"{item['sha256'].removeprefix('sha256:')}  {item['path']}"
        for item in manifest["samples"]
    ]
    write_text(fixture_root / "SHA256SUMS", "\n".join(lines) + "\n")
    return manifest


def check(samples_root: Path, fixture_root: Path, report_path: Path | None) -> bool:
    """复核文件哈希、manifest 与 SHA256SUMS。"""
    lines: list[str] = []
    ok = True

    manifest_path = fixture_root / "samples.manifest.json"
    existing_manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = None
    metadata_by_path = None
    if existing_manifest is not None:
        metadata_by_path = {
            item["path"]: item
            for item in existing_manifest.get("samples", [])
            if isinstance(item, dict) and "path" in item
        }
    expected_manifest = build_manifest(samples_root, metadata_by_path)
    expected_text = json_text(expected_manifest)
    actual_text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
    if actual_text != expected_text:
        ok = False
        lines.append("FAIL samples.manifest.json 与当前文件重算结果不一致")
    else:
        lines.append(f"PASS samples.manifest.json 一致（{expected_manifest['counts']['total_files']} 个文件）")

    expected_sums = "\n".join(
        f"{item['sha256'].removeprefix('sha256:')}  {item['path']}"
        for item in expected_manifest["samples"]
    ) + "\n"
    sums_path = fixture_root / "SHA256SUMS"
    actual_sums = sums_path.read_text(encoding="utf-8") if sums_path.exists() else ""
    if actual_sums != expected_sums:
        ok = False
        lines.append("FAIL SHA256SUMS 与当前文件重算结果不一致")
    else:
        lines.append("PASS SHA256SUMS 一致")

    for item in expected_manifest["samples"]:
        path = samples_root / item["path"]
        if not path.exists():
            ok = False
            lines.append(f"FAIL 缺少文件 {item['path']}")
            continue
        actual_hash = "sha256:" + sha256_file(path)
        if actual_hash != item["sha256"]:
            ok = False
            lines.append(f"FAIL 哈希不一致 {item['path']}")
        if path.stat().st_size != item["size_bytes"]:
            ok = False
            lines.append(f"FAIL 大小不一致 {item['path']}")
    if ok:
        lines.append("PASS 逐文件 SHA256 与大小一致")
    else:
        lines.append("FAIL 存在文件哈希或大小不一致")

    text = "\n".join(lines) + "\n"
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(report_path, text)
    print(text, end="")
    return ok


def create_large(samples_root: Path, mib: int) -> Path:
    """按需生成确定性超大文件，用于 413/分片/超时测试。"""
    path = samples_root / "large" / f"oversize_{mib}mib.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk = bytes(range(256)) * 4096  # 1 MiB
    with open(path, "wb") as handle:
        for _ in range(mib):
            handle.write(chunk)
    write_text(
        samples_root / "large" / "README.md",
        "# 按需生成的大文件\n\n"
        f"- 文件：`oversize_{mib}mib.bin`\n"
        "- 用途：验证 413 `file_too_large`、分片上限、会话过期与超时。\n"
        "- 不进入固定 manifest / SHA256SUMS；每次生成后需单独记录哈希与大小。\n",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 DDP 阶段 2 合成测试样例包")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "samples",
        help="样例输出目录，默认 fixtures/stage2/samples",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="manifest 与 SHA256SUMS 输出目录，默认 fixtures/stage2",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只复核现有样例与 manifest，不重新生成",
    )
    parser.add_argument(
        "--with-large",
        action="store_true",
        help="额外生成按需大文件（默认关闭）",
    )
    parser.add_argument(
        "--large-mib",
        type=int,
        default=16,
        help="按需大文件大小（MiB），默认 16",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="将 --check 结果写入指定报告文件",
    )
    args = parser.parse_args()

    samples_root = args.output.resolve()
    fixture_root = args.fixture_root.resolve()

    if args.check:
        if not samples_root.exists():
            print(f"FAIL 样例目录不存在：{samples_root}", file=sys.stderr)
            return 2
        return 0 if check(samples_root, fixture_root, args.report) else 1

    # 重新生成：只清理本脚本管理的固定样例目录内容，避免残留旧文件影响清单。
    if samples_root.exists():
        for path in sorted(samples_root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    samples_root.mkdir(parents=True, exist_ok=True)
    REGISTRY.clear()

    build_samples(samples_root)
    if args.with_large:
        large_path = create_large(samples_root, args.large_mib)
        print(f"已按需生成大文件：{large_path}")

    manifest = write_manifest_and_sums(samples_root, fixture_root)
    print(
        "生成完成："
        f"{manifest['counts']['total_files']} 个文件，"
        f"{manifest['counts']['total_bytes']} 字节；"
        f"manifest={fixture_root / 'samples.manifest.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
