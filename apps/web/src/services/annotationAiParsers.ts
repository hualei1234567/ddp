/**
 * AI 预标注建议的响应解析。
 *
 * 依据：`backend/app/api/v1/annotation_ai_models.py` 的实际响应
 * （`AiSuggestionOut` / `AgreementOut`）。契约 1.8.0 已收录前三个端点，
 * `agreement` 目前是运行时独有字段，解析按后端实际结构严格校验。
 *
 * 类型放这里而不放 `types/annotation.ts`，是为了遵守本次任务的改动边界；
 * 后续若扩展契约类型，可整体迁入 `types/`。
 */
import type { CursorPage } from "../types/api";
import { readJsonValue } from "./annotationParseBase";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readInteger,
  readNumber,
  readOptionalNumber,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

export interface AiSuggestion {
  suggestion_id: string;
  project_id: string;
  annotation_id: string;
  label_key: string;
  /** 模型给出的建议值；允许任意 JSON（含 null），不做类型收窄。 */
  suggested_value: unknown;
  model_id: string;
  model_version: string;
  prompt_version: string;
  confidence: number;
  generated_at: string;
  human_confirmed: boolean;
  /** 人工修订值；`null` 表示尚未决定或直接采纳。 */
  human_value: unknown | null;
  decided_by: string | null;
  decided_at: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface SuggestionAgreementItem {
  model_id: string;
  model_version: string;
  total: number;
  accepted: number;
  revised: number;
  pending: number;
  decided: number;
  /** `null` 表示没有任何已决定条目——不是 0%。 */
  acceptance_rate: number | null;
}

export interface SuggestionAgreement {
  items: SuggestionAgreementItem[];
}

export function parseAiSuggestion(
  value: unknown,
  path = "AI 建议",
): AiSuggestion {
  const record = asRecord(value, path);
  const confidence = readNumber(record, "confidence", `${path}.confidence`);
  if (confidence < 0 || confidence > 1) {
    throw new ContractValidationError(`${path}.confidence 必须在 [0, 1] 内`);
  }
  return {
    suggestion_id: readUuid(
      record,
      "suggestion_id",
      `${path}.suggestion_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    annotation_id: readUuid(
      record,
      "annotation_id",
      `${path}.annotation_id`,
    ),
    label_key: readString(record, "label_key", `${path}.label_key`),
    suggested_value: readJsonValue(
      record,
      "suggested_value",
      `${path}.suggested_value`,
    ),
    model_id: readString(record, "model_id", `${path}.model_id`),
    model_version: readString(
      record,
      "model_version",
      `${path}.model_version`,
    ),
    prompt_version: readString(
      record,
      "prompt_version",
      `${path}.prompt_version`,
    ),
    confidence,
    generated_at: readString(record, "generated_at", `${path}.generated_at`),
    human_confirmed: readBoolean(
      record,
      "human_confirmed",
      `${path}.human_confirmed`,
    ),
    human_value: readJsonValue(
      record,
      "human_value",
      `${path}.human_value`,
    ),
    decided_by: readOptionalUuid(
      record,
      "decided_by",
      `${path}.decided_by`,
    ),
    decided_at: readOptionalString(
      record,
      "decided_at",
      `${path}.decided_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseAiSuggestionPage(
  value: unknown,
): CursorPage<AiSuggestion> {
  return parseCursorPage(value, "AI 建议列表", parseAiSuggestion);
}

function parseAgreementItem(
  value: unknown,
  path: string,
): SuggestionAgreementItem {
  const record = asRecord(value, path);
  return {
    model_id: readString(record, "model_id", `${path}.model_id`),
    model_version: readString(
      record,
      "model_version",
      `${path}.model_version`,
    ),
    total: readInteger(record, "total", `${path}.total`),
    accepted: readInteger(record, "accepted", `${path}.accepted`),
    revised: readInteger(record, "revised", `${path}.revised`),
    pending: readInteger(record, "pending", `${path}.pending`),
    decided: readInteger(record, "decided", `${path}.decided`),
    acceptance_rate: readOptionalNumber(
      record,
      "acceptance_rate",
      `${path}.acceptance_rate`,
    ),
  };
}

export function parseSuggestionAgreement(
  value: unknown,
  path = "AI 建议一致率",
): SuggestionAgreement {
  const record = asRecord(value, path);
  const items = record.items;
  if (!Array.isArray(items)) {
    throw new ContractValidationError(`${path}.items 必须是数组`);
  }
  return {
    items: items.map((item, index) =>
      parseAgreementItem(item, `${path}.items[${index}]`),
    ),
  };
}
