import type {
  AssetFormat,
  DownloadUrl,
  RawAsset,
  RawAssetPage,
  RawAssetStatus,
} from "../types/collection";
import { apiClient, type ApiClient } from "./api";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";
import {
  parseDownloadUrl,
  parseRawAsset,
  parseRawAssetPage,
} from "./uploadParsers";

export interface RawAssetListOptions extends PageRequestOptions {
  status?: RawAssetStatus;
  collectionTaskId?: string;
  format?: AssetFormat;
}

export interface RawAssetsApi {
  listAssets: (
    projectId: string,
    options?: RawAssetListOptions,
  ) => Promise<RawAssetPage>;
  getAsset: (
    projectId: string,
    assetId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<RawAsset>;
  /** 获取短期下载地址；URL 只在内存中使用，不落盘、不写日志。 */
  getDownloadUrl: (
    projectId: string,
    assetId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<DownloadUrl>;
}

/**
 * 阶段 2 RawAsset 查询 API（DRAFT）。
 *
 * 列表与详情都执行项目归属一致性校验；权限与可见性以服务端为准。
 * 本模块只读，不提供删除/墓碑等高风险操作。
 */
export function createRawAssetsApi(client: ApiClient): RawAssetsApi {
  return {
    async listAssets(projectId, options) {
      return client.get(projectPath(projectId, "/raw-assets"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          collection_task_id: options?.collectionTaskId,
          format: options?.format,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            parseRawAssetPage(value),
            projectId,
            "原始资产列表",
          ),
      });
    },

    async getAsset(projectId, assetId, options) {
      return client.get(
        projectPath(
          projectId,
          `/raw-assets/${requireIdentifier(assetId, "asset_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseRawAsset(value), projectId, "原始资产"),
        },
      );
    },

    async getDownloadUrl(projectId, assetId, options) {
      return client.get(
        `/api/v1/raw-assets/${requireIdentifier(assetId, "asset_id")}/download-url`,
        {
          signal: options?.signal,
          query: { project_id: projectId },
          validate: (value) => parseDownloadUrl(value),
        },
      );
    },
  };
}

export const rawAssetsApi = createRawAssetsApi(apiClient);
