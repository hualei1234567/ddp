/** 阶段 9 场地响应解析器。 */

import type { CursorPage } from "../types/api";
import { LOCATION_KIND_VALUES, type Location } from "../types/location";
import {
  asRecord,
  parseCursorPage,
  readEnum,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

export function parseLocation(value: unknown, path = "location"): Location {
  const record = asRecord(value, path);
  return {
    location_id: readUuid(record, "location_id", path),
    project_id: readUuid(record, "project_id", path),
    code: readString(record, "code", path),
    name: readString(record, "name", path),
    // 契约声明了枚举；非法取值由客户端直接拒绝，不当成「未知类型」透传。
    kind: readEnum(record, "kind", path, LOCATION_KIND_VALUES),
    address: readOptionalString(record, "address", path),
    notes: readOptionalString(record, "notes", path),
    version: readPositiveInteger(record, "version", path),
    created_at: readOptionalString(record, "created_at", path),
    updated_at: readOptionalString(record, "updated_at", path),
    created_by: readOptionalUuid(record, "created_by", path),
    updated_by: readOptionalUuid(record, "updated_by", path),
  };
}

export function parseLocationPage(value: unknown): CursorPage<Location> {
  return parseCursorPage(value, "locations", parseLocation);
}
