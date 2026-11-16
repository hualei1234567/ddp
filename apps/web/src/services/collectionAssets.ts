/**
 * 阶段 8 采集素材 API client（四类素材统一入口）。
 *
 * 端点形状完全一致，只有资源段与响应模型不同，因此按 `kind` 分派而不是
 * 复制四套 client；页面也只有一个泛型实现。
 *
 * 协议约束（契约 `paths.stage8.yaml`）：
 * - 创建：`Idempotency-Key`；
 * - PATCH：`If-Match`；
 * - 状态动作：`If-Match` + `Idempotency-Key`，body 可选 `reason`；
 * - 删除：无前置头，`204` 无响应体。
 */

import type { CursorPage } from "../types/api";
import type {
  CollectionAsset,
  CollectionAssetCreateInput,
  CollectionAssetKind,
  CollectionAssetPatchInput,
  ScenarioPatchInput,
} from "../types/collectionAsset";
import { apiClient, type ApiClient } from "./api";
import {
  parseActionSpec,
  parseActionSpecPage,
  parseCollectionPlan,
  parseCollectionPlanPage,
  parseCollectionSop,
  parseCollectionSopPage,
  parseScenario,
  parseScenarioPage,
} from "./collectionAssetParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface CollectionAssetListOptions extends PageRequestOptions {
  status?: string;
}

/** 创建只需要幂等键与取消信号，没有 If-Match。 */
export interface AssetCreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

/** 更新与状态动作都必须带当前 version（`If-Match`）。 */
export interface AssetWriteOptions {
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
  reason?: string | null;
}

interface AssetConfig {
  /** 契约里的复数资源段。 */
  segment: string;
  /** 用于错误文案的中文名。 */
  label: string;
  parse: (value: unknown) => CollectionAsset;
  parsePage: (value: unknown) => CursorPage<CollectionAsset>;
}

const COLLECTION_ASSET_CONFIG: Record<CollectionAssetKind, AssetConfig> = {
  plans: {
    segment: "collection-plans",
    label: "采集方案",
    parse: (value) => parseCollectionPlan(value),
    parsePage: parseCollectionPlanPage,
  },
  sops: {
    segment: "collection-sops",
    label: "SOP",
    parse: (value) => parseCollectionSop(value),
    parsePage: parseCollectionSopPage,
  },
  scenarios: {
    segment: "scenarios",
    label: "场景",
    parse: (value) => parseScenario(value),
    parsePage: parseScenarioPage,
  },
  "action-specs": {
    segment: "action-specs",
    label: "动作库",
    parse: (value) => parseActionSpec(value),
    parsePage: parseActionSpecPage,
  },
};

export function isCollectionAssetKind(value: string): value is CollectionAssetKind {
  return Object.prototype.hasOwnProperty.call(COLLECTION_ASSET_CONFIG, value);
}

export function collectionAssetLabel(kind: CollectionAssetKind): string {
  return COLLECTION_ASSET_CONFIG[kind].label;
}

export interface CollectionAssetApi {
  list(
    kind: CollectionAssetKind,
    projectId: string,
    options?: CollectionAssetListOptions,
  ): Promise<CursorPage<CollectionAsset>>;
  get(
    kind: CollectionAssetKind,
    projectId: string,
    assetId: string,
    options?: { signal?: AbortSignal },
  ): Promise<CollectionAsset>;
  create(
    kind: CollectionAssetKind,
    projectId: string,
    input: CollectionAssetCreateInput,
    options?: AssetCreateOptions,
  ): Promise<CollectionAsset>;
  update(
    kind: CollectionAssetKind,
    projectId: string,
    assetId: string,
    patch: CollectionAssetPatchInput | ScenarioPatchInput,
    options: AssetWriteOptions,
  ): Promise<CollectionAsset>;
  act(
    kind: CollectionAssetKind,
    projectId: string,
    assetId: string,
    action: string,
    options: AssetWriteOptions,
  ): Promise<CollectionAsset>;
  remove(
    kind: CollectionAssetKind,
    projectId: string,
    assetId: string,
    options?: { signal?: AbortSignal },
  ): Promise<void>;
}

function assetPath(
  kind: CollectionAssetKind,
  projectId: string,
  assetId: string,
  suffix = "",
): string {
  const config = COLLECTION_ASSET_CONFIG[kind];
  return projectPath(
    projectId,
    `/${config.segment}/${requireIdentifier(assetId, "asset_id")}${suffix}`,
  );
}

export function createCollectionAssetApi(client: ApiClient): CollectionAssetApi {
  return {
    async list(kind, projectId, options) {
      const config = COLLECTION_ASSET_CONFIG[kind];
      return client.get(projectPath(projectId, `/${config.segment}`), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            config.parsePage(value),
            projectId,
            `${config.label}列表`,
          ),
      });
    },

    async get(kind, projectId, assetId, options) {
      const config = COLLECTION_ASSET_CONFIG[kind];
      return client.get(assetPath(kind, projectId, assetId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(config.parse(value), projectId, config.label),
      });
    },

    async create(kind, projectId, input, options) {
      const config = COLLECTION_ASSET_CONFIG[kind];
      return client.request(projectPath(projectId, `/${config.segment}`), {
        method: "POST",
        signal: options?.signal,
        headers: {
          "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey(),
        },
        body: input,
        validate: (value) =>
          assertProjectScope(config.parse(value), projectId, config.label),
      });
    },

    async update(kind, projectId, assetId, patch, options) {
      const config = COLLECTION_ASSET_CONFIG[kind];
      // PATCH 契约只声明 If-Match；不额外发送幂等键。
      return client.request(assetPath(kind, projectId, assetId), {
        method: "PATCH",
        signal: options.signal,
        headers: { "If-Match": formatIfMatch(options.version) },
        body: patch,
        validate: (value) =>
          assertProjectScope(config.parse(value), projectId, config.label),
      });
    },

    async act(kind, projectId, assetId, action, options) {
      const config = COLLECTION_ASSET_CONFIG[kind];
      return client.request(
        assetPath(
          kind,
          projectId,
          assetId,
          `/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: {
            "If-Match": formatIfMatch(options.version),
            "Idempotency-Key":
              options.idempotencyKey ?? createIdempotencyKey(),
          },
          // reason 可选；显式 null 与「未提交」在服务端语义不同，这里始终提交，
          // 保持动作可审计（服务端不强制填写原因）。
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(config.parse(value), projectId, config.label),
        },
      );
    },

    async remove(kind, projectId, assetId, options) {
      await client.request(assetPath(kind, projectId, assetId), {
        method: "DELETE",
        signal: options?.signal,
      });
    },
  };
}

export const collectionAssetApi = createCollectionAssetApi(apiClient);
