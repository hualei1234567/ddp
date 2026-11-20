import type { CursorPage } from "./api";

export const CAPTURE_SESSION_STATUS_VALUES = ["SCHEDULED", "RUNNING", "PAUSED", "COMPLETED", "ABORTED"] as const;
export const CAPTURE_SESSION_ACTION_VALUES = ["start", "pause", "resume", "complete", "abort"] as const;
export const CAPTURE_SESSION_ARTIFACT_KIND_VALUES = ["RAW_ASSET", "EPISODE"] as const;

export type CaptureSessionStatus = (typeof CAPTURE_SESSION_STATUS_VALUES)[number];
export type CaptureSessionAction = (typeof CAPTURE_SESSION_ACTION_VALUES)[number];
export type CaptureSessionArtifactKind = (typeof CAPTURE_SESSION_ARTIFACT_KIND_VALUES)[number];

export interface CaptureSession {
  session_id: string; project_id: string; code: string;
  task_id: string | null; batch_id: string | null; operator_id: string | null;
  robot_id: string | null; location_id: string | null; status: CaptureSessionStatus;
  started_at: string | null; ended_at: string | null; status_reason: string | null;
  allowed_actions: string[]; version: number; created_at: string | null;
  updated_at: string | null; created_by: string | null; updated_by: string | null;
}

export interface CaptureSessionCreateInput {
  code: string;
  task_id?: string | null;
  batch_id?: string | null;
  operator_id?: string | null;
  robot_id?: string | null;
  location_id?: string | null;
}

export interface CaptureSessionArtifact {
  kind: CaptureSessionArtifactKind; artifact_id: string; code: string;
  status: string; created_at: string;
}

export type CaptureSessionPage = CursorPage<CaptureSession>;
export type CaptureSessionArtifactPage = CursorPage<CaptureSessionArtifact>;
