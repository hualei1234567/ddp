/**
 * 阶段 9 场地 API client。
 *
 * 契约：`packages/contracts/fragments/paths.stage9.yaml`。
 * 只有「创建 / 列表 / 详情 / 修改」——后端刻意没有 DELETE 与 status 字段，
 * 前端不补。
 */

import type { CursorPage } from "../types/api";
import type {
  Location,
  LocationCreateInput,
  LocationKind,
  LocationPatchInput,
} from "../types/location";
import { apiClient, type ApiClient } from "./api";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import { parseLocation, parseLocationPage } from "./locationParsers";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface LocationListOptions extends PageRequestOptions {
  kind?: LocationKind;
}

export interface LocationCreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface LocationUpdateOptions {
  version: number;
  signal?: AbortSignal;
}

export interface LocationApi {
  list(
    projectId: string,
    options?: LocationListOptions,
  ): Promise<CursorPage<Location>>;
  create(
    projectId: string,
    input: LocationCreateInput,
    options?: LocationCreateOptions,
  ): Promise<Location>;
  get(
    projectId: string,
    locationId: string,
    options?: { signal?: AbortSignal },
  ): Promise<Location>;
  update(
    projectId: string,
    locationId: string,
    patch: LocationPatchInput,
    options: LocationUpdateOptions,
  ): Promise<Location>;
}

function locationPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/locations${suffix}`);
}

export function createLocationApi(client: ApiClient): LocationApi {
  const locationOf = (projectId: string, locationId: string) =>
    locationPath(projectId, `/${requireIdentifier(locationId, "location_id")}`);

  return {
    async list(projectId, options) {
      return client.get(locationPath(projectId), {
        signal: options?.signal,
        query: { kind: options?.kind, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseLocationPage(value), projectId, "场地列表"),
      });
    },

    async create(projectId, input, options) {
      return client.request(locationPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: {
          "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey(),
        },
        body: input,
        validate: (value) => assertProjectScope(parseLocation(value), projectId, "场地"),
      });
    },

    async get(projectId, locationId, options) {
      return client.get(locationOf(projectId, locationId), {
        signal: options?.signal,
        validate: (value) => assertProjectScope(parseLocation(value), projectId, "场地"),
      });
    },

    async update(projectId, locationId, patch, options) {
      // PATCH 契约只声明 If-Match；不额外发送幂等键。
      return client.request(locationOf(projectId, locationId), {
        method: "PATCH",
        signal: options.signal,
        headers: { "If-Match": formatIfMatch(options.version) },
        body: patch,
        validate: (value) => assertProjectScope(parseLocation(value), projectId, "场地"),
      });
    },
  };
}

export const locationApi = createLocationApi(apiClient);
