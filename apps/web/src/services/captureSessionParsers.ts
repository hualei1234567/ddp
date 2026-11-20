import type { CursorPage } from "../types/api";
import { CAPTURE_SESSION_ARTIFACT_KIND_VALUES, CAPTURE_SESSION_STATUS_VALUES, type CaptureSession, type CaptureSessionArtifact } from "../types/captureSession";
import { asRecord, parseCursorPage, readEnum, readOptionalString, readOptionalUuid, readPositiveInteger, readString, readStringArray, readUuid } from "./validation";

export function parseCaptureSession(value: unknown, path = "capture_session"): CaptureSession {
  const record = asRecord(value, path);
  return {
    session_id: readUuid(record, "session_id", `${path}.session_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    task_id: readOptionalUuid(record, "task_id", `${path}.task_id`),
    batch_id: readOptionalUuid(record, "batch_id", `${path}.batch_id`),
    operator_id: readOptionalUuid(record, "operator_id", `${path}.operator_id`),
    robot_id: readOptionalUuid(record, "robot_id", `${path}.robot_id`),
    location_id: readOptionalUuid(record, "location_id", `${path}.location_id`),
    status: readEnum(record, "status", `${path}.status`, CAPTURE_SESSION_STATUS_VALUES),
    started_at: readOptionalString(record, "started_at", `${path}.started_at`),
    ended_at: readOptionalString(record, "ended_at", `${path}.ended_at`),
    status_reason: readOptionalString(record, "status_reason", `${path}.status_reason`),
    allowed_actions: readStringArray(record, "allowed_actions", `${path}.allowed_actions`),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseCaptureSessionPage(value: unknown): CursorPage<CaptureSession> {
  return parseCursorPage(value, "capture_sessions", parseCaptureSession);
}

export function parseCaptureSessionArtifact(value: unknown, path = "artifact"): CaptureSessionArtifact {
  const record = asRecord(value, path);
  return {
    kind: readEnum(record, "kind", `${path}.kind`, CAPTURE_SESSION_ARTIFACT_KIND_VALUES),
    artifact_id: readUuid(record, "artifact_id", `${path}.artifact_id`),
    code: readString(record, "code", `${path}.code`),
    status: readString(record, "status", `${path}.status`),
    created_at: readString(record, "created_at", `${path}.created_at`),
  };
}

export function parseCaptureSessionArtifactPage(value: unknown): CursorPage<CaptureSessionArtifact> {
  return parseCursorPage(value, "capture_session_artifacts", parseCaptureSessionArtifact);
}
