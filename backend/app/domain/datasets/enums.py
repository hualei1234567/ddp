"""阶段 4 数据集领域枚举。

取值对齐 ``docs/architecture/contract-draft/dataset/`` 草案；未知值一律拒绝。
"""

from __future__ import annotations

from enum import StrEnum


class DatasetStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class DatasetPurpose(StrEnum):
    TRAINING = "TRAINING"
    EVALUATION = "EVALUATION"
    BENCHMARK = "BENCHMARK"
    OTHER = "OTHER"


class DatasetUsagePolicy(StrEnum):
    TRAINING_ALLOWED = "TRAINING_ALLOWED"
    TRAINING_DENIED = "TRAINING_DENIED"
    UNKNOWN = "UNKNOWN"


class DatasetVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    FROZEN = "FROZEN"
    SUPERSEDED = "SUPERSEDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class ValidityStatus(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


class Split(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


class SampleRole(StrEnum):
    NORMAL = "NORMAL"
    HOLDOUT = "HOLDOUT"
    HARD_CASE = "HARD_CASE"
    EVALUATION = "EVALUATION"


class DedupKey(StrEnum):
    EPISODE_LINEAGE = "EPISODE_LINEAGE"
    MANIFEST_SHA256 = "MANIFEST_SHA256"
    RAW_ASSET_SHA256 = "RAW_ASSET_SHA256"


class RetainRule(StrEnum):
    NEWEST_ACCEPTED = "NEWEST_ACCEPTED"
    HIGHEST_QUALITY = "HIGHEST_QUALITY"
    EARLIEST = "EARLIEST"
    MANUAL = "MANUAL"


class NearDuplicateEnforcement(StrEnum):
    WARN_ONLY = "WARN_ONLY"
    BLOCK = "BLOCK"


class SplitStrategy(StrEnum):
    RATIO = "RATIO"
    FIXED_COUNT = "FIXED_COUNT"


class GroupingKey(StrEnum):
    EPISODE_LINEAGE = "EPISODE_LINEAGE"
    MANIFEST_SHA256 = "MANIFEST_SHA256"
    RAW_ASSET_SHA256 = "RAW_ASSET_SHA256"
    SIMILARITY_GROUP = "SIMILARITY_GROUP"
    SCENE_GROUP = "SCENE_GROUP"


class EmptySplitPolicy(StrEnum):
    FAIL = "FAIL"
    ALLOW_EMPTY_TEST = "ALLOW_EMPTY_TEST"


class RoundingRule(StrEnum):
    FLOOR = "FLOOR"
    ROUND_HALF_UP = "ROUND_HALF_UP"
    LARGEST_REMAINDER = "LARGEST_REMAINDER"


class StratifyKey(StrEnum):
    SCENE = "SCENE"
    ACTION = "ACTION"
    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    LABEL = "LABEL"
    QUALITY_STATUS = "QUALITY_STATUS"


class LeakageRule(StrEnum):
    EPISODE_LINEAGE_CROSS_SPLIT = "EPISODE_LINEAGE_CROSS_SPLIT"
    MANIFEST_SHA256_CROSS_SPLIT = "MANIFEST_SHA256_CROSS_SPLIT"
    RAW_ASSET_SHA256_CROSS_SPLIT = "RAW_ASSET_SHA256_CROSS_SPLIT"
    SIMILARITY_GROUP_CROSS_SPLIT = "SIMILARITY_GROUP_CROSS_SPLIT"
    SCENE_GROUP_CROSS_SPLIT = "SCENE_GROUP_CROSS_SPLIT"
    BATCH_GROUP_CROSS_SPLIT = "BATCH_GROUP_CROSS_SPLIT"
    TEMPORAL_OVERLAP_CROSS_SPLIT = "TEMPORAL_OVERLAP_CROSS_SPLIT"
    DUPLICATE_ANNOTATION = "DUPLICATE_ANNOTATION"


HARD_LEAKAGE_RULES: frozenset[LeakageRule] = frozenset(
    {
        LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,
        LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,
        LeakageRule.RAW_ASSET_SHA256_CROSS_SPLIT,
        LeakageRule.SIMILARITY_GROUP_CROSS_SPLIT,
    }
)
SOFT_LEAKAGE_RULES: frozenset[LeakageRule] = frozenset(
    {
        LeakageRule.SCENE_GROUP_CROSS_SPLIT,
        LeakageRule.BATCH_GROUP_CROSS_SPLIT,
        LeakageRule.TEMPORAL_OVERLAP_CROSS_SPLIT,
        LeakageRule.DUPLICATE_ANNOTATION,
    }
)


class LeakageStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - 状态值不是凭据
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    WAIVED = "WAIVED"


class QualityReportStatus(StrEnum):
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"


class ExportJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"


class ExportFormat(StrEnum):
    """训练导出格式。

    - ``LE_ROBOT``：LeRobot 元数据 profile（``frame_level_mapping: false``，
      ``verification_status: UNVERIFIED``——尚未通过真实 Loader 验证）；
    - ``CANONICAL``：平台自己的规范格式（Canonical JSON + NDJSON），
      语义与 LeRobot 不同，下游据此判断能否直接喂给 LeRobot Loader。

    未知格式必须显式拒绝，不得静默回退。
    """

    LE_ROBOT = "LE_ROBOT"
    CANONICAL = "CANONICAL"


class ExportVerificationStatus(StrEnum):
    """Loader 验证结论，由真实 LeRobot Loader 实测决定，不得凭结构合规宣称。

    与 :class:`ExportLoadableScope` 正交：``loadable_scope`` 描述产物结构上
    包含什么（元数据 / 逐帧），``verification_status`` 描述 Loader 是否真的
    加载成功过。结构合规不等于可加载，更不等于可训练。
    """

    UNVERIFIED = "UNVERIFIED"
    VERIFIED_BY_LOADER = "VERIFIED_BY_LOADER"


class ExportLoadableScope(StrEnum):
    """导出产物在结构上的可加载范围（V31-042 切片 1，契约开放枚举）。

    - ``METADATA_ONLY``：产物含 LeRobot 必需元数据文件，但不含逐帧
      ``observation.*`` / ``action`` 列，只能被 Loader 读出 episode 结构，
      不能直接用于训练；
    - ``FRAMES``：含逐帧特征列，具备直接训练的结构条件（仍须 Loader 实测
      通过后才可置 ``verification_status = VERIFIED_BY_LOADER``）。

    客户端必须容忍未知取值（取值集合随逐帧链路落地可增长）。
    """

    METADATA_ONLY = "METADATA_ONLY"
    FRAMES = "FRAMES"


#: 每种导出格式的当前产物范围。在逐帧数据链路（V31-044）落地前，
#: LE_ROBOT 与 CANONICAL 产物都只到元数据层；此处是唯一事实来源，
#: 导出侧与 API 序列化都从这里派生，禁止各写一份导致漂移。
EXPORT_FORMAT_LOADABLE_SCOPE: dict[ExportFormat, ExportLoadableScope] = {
    ExportFormat.LE_ROBOT: ExportLoadableScope.METADATA_ONLY,
    ExportFormat.CANONICAL: ExportLoadableScope.METADATA_ONLY,
}


class ExclusionCode(StrEnum):
    EPISODE_STATUS_INELIGIBLE = "EPISODE_STATUS_INELIGIBLE"
    QUALITY_NOT_PASSED = "QUALITY_NOT_PASSED"
    QUALITY_WARN_UNCONFIRMED = "QUALITY_WARN_UNCONFIRMED"
    # 统一质量引擎（quality_runs）门禁：只看 Episode 的最新运行结论。
    QUALITY_RUN_MISSING = "QUALITY_RUN_MISSING"
    QUALITY_RUN_NOT_PASSED = "QUALITY_RUN_NOT_PASSED"
    QUALITY_SCORE_MISSING = "QUALITY_SCORE_MISSING"
    QUALITY_SCORE_BELOW_THRESHOLD = "QUALITY_SCORE_BELOW_THRESHOLD"
    ANNOTATION_NOT_APPROVED = "ANNOTATION_NOT_APPROVED"
    ANNOTATION_SNAPSHOT_MISMATCH = "ANNOTATION_SNAPSHOT_MISMATCH"
    MANIFEST_MISSING = "MANIFEST_MISSING"
    MANIFEST_HASH_INVALID = "MANIFEST_HASH_INVALID"
    RAW_ASSET_MISSING = "RAW_ASSET_MISSING"
    RAW_ASSET_HASH_INVALID = "RAW_ASSET_HASH_INVALID"
    RAW_ASSET_NOT_USABLE = "RAW_ASSET_NOT_USABLE"
    TIME_SYNC_UNKNOWN = "TIME_SYNC_UNKNOWN"
    TIME_SYNC_INVALID = "TIME_SYNC_INVALID"
    TIME_SYNC_DEGRADED_UNCONFIRMED = "TIME_SYNC_DEGRADED_UNCONFIRMED"
    CALIBRATION_MISSING = "CALIBRATION_MISSING"
    CALIBRATION_INVALID = "CALIBRATION_INVALID"
    USAGE_POLICY_DENIED = "USAGE_POLICY_DENIED"
    USAGE_POLICY_UNKNOWN = "USAGE_POLICY_UNKNOWN"
    DUPLICATE_EPISODE_LINEAGE = "DUPLICATE_EPISODE_LINEAGE"
    DUPLICATE_MANIFEST_SHA256 = "DUPLICATE_MANIFEST_SHA256"
    DUPLICATE_RAW_ASSET_SHA256 = "DUPLICATE_RAW_ASSET_SHA256"
