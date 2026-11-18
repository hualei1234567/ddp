import type {
  LineageDirection,
  LineageNodeType,
  LineageRelation,
} from "../types/collection";
import {
  buildEpisodeDetailPath,
  buildRawAssetDetailPath,
  buildTaskDetailPath,
} from "./collectionUrl";

export const LINEAGE_NODE_LABELS: Record<LineageNodeType, string> = {
  RAW_ASSET: "原始资产",
  UPLOAD_SESSION: "上传会话",
  COLLECTION_TASK: "采集任务",
  COLLECTION_BATCH: "采集批次",
  INGESTION_JOB: "接入任务",
  EPISODE: "Episode",
  QUALITY_RUN: "质检运行",
  ANNOTATION: "标注",
  DATASET_VERSION: "数据集版本",
  TRAINING_RUN: "训练运行",
  DATA_SOURCE: "数据源",
  DATA_SOURCE_IMPORT: "数据源导入任务",
};

export const LINEAGE_RELATION_LABELS: Record<LineageRelation, string> = {
  UPLOADED_BY: "由上传会话登记",
  PRODUCED_BY: "来源任务/批次",
  USED_AS_INPUT: "作为接入输入",
  PRODUCED: "接入产物",
  SEGMENTED_INTO: "切分为",
  SUPERSEDES: "取代",
  INVALIDATES: "失效传播",
  EVALUATED_BY: "被质检",
  ANNOTATED_BY: "被标注",
  INCLUDED_IN: "纳入数据集",
  TRAINED_ON: "用于训练",
};

export const LINEAGE_DIRECTION_LABELS: Record<LineageDirection, string> = {
  forward: "正向（下游）",
  reverse: "反向（上游）",
};

/** 已开放详情页的节点类型返回站内路径；其余节点只展示 ID。 */
export function lineageNodePath(
  projectId: string,
  nodeType: LineageNodeType,
  nodeId: string,
): string | null {
  switch (nodeType) {
    case "EPISODE":
      return buildEpisodeDetailPath(projectId, nodeId);
    case "RAW_ASSET":
      return buildRawAssetDetailPath(projectId, nodeId);
    case "COLLECTION_TASK":
      return buildTaskDetailPath(projectId, nodeId);
    default:
      return null;
  }
}
