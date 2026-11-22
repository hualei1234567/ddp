import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ReactNode } from "react";

import type {
  AnnotationFeedback,
} from "../../services/annotationPresentation";
import {
  presentAnnotationStatus,
  presentProgress,
} from "../../services/annotationPresentation";
import type {
  DraftFieldValue,
  FieldIssue,
  EpisodeFormValues,
} from "../../services/annotationValidation";
import type { WorkbenchPartialError, WorkbenchData } from "../../services/annotationWorkbench";
import { DATA_SOURCE_VALUES, type DataSource } from "../../types/annotation";
import type { ApiError } from "../../services/api";
import type { PagePhase } from "../../services/pagePhase";
import type { DraftLoadStatus } from "../../services/annotationDraft";
import type { DraftSaveState } from "../../hooks/useAnnotationDraft";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationFeedbackAlert from "./AnnotationFeedbackAlert";
import AnnotationStatusTag from "./AnnotationStatusTag";
import EpisodeEditor from "./EpisodeEditor";

export interface WorkbenchBodyProps {
  /** 服务端强制项目隔离；所有子请求都必须显式带上项目上下文。 */
  projectId: string;
  data: WorkbenchData | null;
  phase: PagePhase;
  error: ApiError | null;
  stale: boolean;
  partialErrors: WorkbenchPartialError[];
  online: boolean;
  values: Record<string, EpisodeFormValues>;
  draftReady: boolean;
  draftNotice: DraftLoadStatus | null;
  saveState: DraftSaveState;
  storageAvailable: boolean;
  draftUpdatedAt: number | null;
  validationIssues: FieldIssue[];
  validationAttempted: boolean;
  dataSource: DataSource;
  disabledReason: string | null;
  hiddenRequiredLabels: boolean;
  busyAction: string | null;
  feedback: AnnotationFeedback | null;
  onFieldChange: (
    episodeId: string,
    labelKey: string,
    value: DraftFieldValue,
  ) => void;
  onDataSourceChange: (value: DataSource) => void;
  onSubmit: () => void;
  onSubmitDraft: () => void;
  onWithdrawDraft: () => void;
  onResetDraft: () => void;
  onRetry: () => void;
  onBackToList: () => void;
}

function SaveStateText({
  state,
  updatedAt,
}: {
  state: DraftSaveState;
  updatedAt: number | null;
}): ReactNode {
  if (state === "saving") {
    return "草稿保存中…";
  }
  if (state === "saved") {
    return `草稿已保存${updatedAt ? `（${new Date(updatedAt).toLocaleTimeString()}）` : ""}`;
  }
  if (state === "failed") {
    return "草稿保存失败：本地存储不可用或空间不足，请勿关闭页面。";
  }
  if (state === "unavailable") {
    return "本地存储不可用；草稿仅在当前页面保留。";
  }
  return "草稿会在输入后自动保存到本机。";
}

function DraftNotice({ notice }: { notice: DraftLoadStatus | null }): ReactNode {
  if (notice === "incompatible") {
    return (
      <PageState
        compact
        variant="stale"
        title="本地草稿与当前 Schema 快照不一致"
        description="未加载旧草稿，避免把旧版本标签值写入新 Schema。请重新填写或清除草稿。"
      />
    );
  }
  if (notice === "invalid") {
    return (
      <PageState
        compact
        variant="failed"
        title="本地草稿无法解析"
        description="草稿内容已损坏，未加载；可重新填写或清除草稿。"
      />
    );
  }
  if (notice === "ready") {
    return (
      <Alert
        type="info"
        showIcon
        message="已恢复本地草稿"
        description="草稿仅保存在本机浏览器，尚未提交到服务端。"
      />
    );
  }
  return null;
}

function RecordPreview({
  data,
}: {
  data: WorkbenchData;
}) {
  const records = data.existingDraft?.records ?? [];
  if (records.length === 0) {
    return (
      <Typography.Text type="secondary">
        服务端草稿未返回标注记录，请刷新后重试。
      </Typography.Text>
    );
  }
  return (
    <Space direction="vertical" size={10} className="ddp-stack">
      {records.map((record) => (
        <Card
          key={record.annotation_id}
          size="small"
          title={<IdentifierValue value={record.episode_id} label="Episode ID" />}
        >
          {record.labels.map((label) => (
            <div key={label.label_key}>
              <Typography.Text code>{label.label_key}</Typography.Text>
              <Typography.Text>
                {" "}
                {typeof label.value === "string"
                  ? label.value
                  : JSON.stringify(label.value)}
              </Typography.Text>
              <Tag>{label.source}</Tag>
            </div>
          ))}
        </Card>
      ))}
    </Space>
  );
}

function Summary({ data }: { data: WorkbenchData }) {
  return (
    <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
      <Descriptions.Item label="任务">
        <Space direction="vertical" size={0}>
          <span>{data.task.name}</span>
          <Typography.Text type="secondary">{data.task.code}</Typography.Text>
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="任务状态">
        <AnnotationStatusTag value={data.task.status} />
      </Descriptions.Item>
      <Descriptions.Item label="优先级">{data.task.priority}</Descriptions.Item>
      <Descriptions.Item label="分配">
        <IdentifierValue value={data.assignment.assignment_id} label="分配 ID" />
      </Descriptions.Item>
      <Descriptions.Item label="分配状态">
        <AnnotationStatusTag value={data.assignment.status} />
      </Descriptions.Item>
      <Descriptions.Item label="批次">
        <Space direction="vertical" size={0}>
          <span>
            #{data.batch.sequence_no} {data.batch.code}
          </span>
          <AnnotationStatusTag value={data.batch.status} />
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="Schema">
        {data.schema.code} v{data.schema.schema_version}
      </Descriptions.Item>
      <Descriptions.Item label="截止时间">
        {data.task.due_at ?? "—"}
      </Descriptions.Item>
      <Descriptions.Item label="进度">
        已提交 {presentProgress(data.task.progress, "submitted_items")} / 目标{" "}
        {data.task.target_item_count}
      </Descriptions.Item>
    </Descriptions>
  );
}

/**
 * 工作台主体：只负责渲染状态与表单，不直接发请求。
 * 表单值、完整性校验与提交由页面/Hook 统一编排。
 */
export default function WorkbenchBody({
  projectId,
  data,
  phase,
  error,
  stale,
  partialErrors,
  online,
  values,
  draftReady,
  draftNotice,
  saveState,
  storageAvailable,
  draftUpdatedAt,
  validationIssues,
  validationAttempted,
  dataSource,
  disabledReason,
  hiddenRequiredLabels,
  busyAction,
  feedback,
  onFieldChange,
  onDataSourceChange,
  onSubmit,
  onSubmitDraft,
  onWithdrawDraft,
  onResetDraft,
  onRetry,
  onBackToList,
}: WorkbenchBodyProps) {
  if (phase === "forbidden" || phase === "not-found") {
    return error ? (
      <ApiErrorState error={error} onRetry={onRetry} />
    ) : (
      <PageState variant="forbidden" onRetry={onRetry} />
    );
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载标注工作台"
        description="正在读取分配、任务、Schema 与 Episode。"
      />
    );
  }
  if (phase === "error" || data === null) {
    return error ? (
      <ApiErrorState error={error} onRetry={onRetry} />
    ) : (
      <PageState variant="error" onRetry={onRetry} />
    );
  }

  const definitions = data.schema.labels.filter(
    (label) => label.visibility === "ANNOTATOR",
  );
  const issuesByField = new Map(
    validationIssues.map((issue) => [issue.field, issue.message]),
  );
  const issuesForEpisode = (episodeId: string): Record<string, string> => {
    const result: Record<string, string> = {};
    if (!validationAttempted) {
      return result;
    }
    for (const [field, message] of issuesByField) {
      if (field.startsWith(`${episodeId}.`)) {
        result[field.slice(episodeId.length + 1)] = message;
      }
    }
    return result;
  };
  const recordIssues = validationAttempted
    ? validationIssues.filter((issue) => issue.field.endsWith(".__record__"))
    : [];
  const showForm =
    data.existingDraft === null &&
    (data.latestSubmission === null ||
      ["REJECTED", "REWORK_REQUIRED", "RESOLVED_REWORK", "RESOLVED_REJECTED", "WITHDRAWN"].includes(
        data.latestSubmission.status,
      ));

  const submitDisabledReason =
    disabledReason ??
    (!online ? "网络离线，无法提交；恢复联网后可继续。" : null) ??
    (!draftReady ? "草稿尚未加载完成。" : null);

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，当前展示的是上次成功获取的工作台数据。"
          onRetry={onRetry}
        />
      ) : null}
      {partialErrors.length > 0 ? (
        <PageState
          compact
          variant="partial"
          title="部分数据加载失败"
          description={
            <span>
              {partialErrors
                .map(
                  (item) =>
                    `${item.scope}（${item.error.code ?? "unknown"}）`,
                )
                .join("；")}
              。已展示可用数据，请重试或联系管理员。
            </span>
          }
          onRetry={onRetry}
        />
      ) : null}
      <AnnotationFeedbackAlert feedback={feedback} />
      {!online ? (
        <Alert
          type="warning"
          showIcon
          message="网络离线"
          description="本地草稿会继续保存；提交与刷新需要联网。"
        />
      ) : null}
      {hiddenRequiredLabels ? (
        <PageState
          compact
          variant="partial"
          title="Schema 含当前角色不可见且必填的标签"
          description="标注员无法填写这些标签，提交会被服务端拒绝。请联系标注管理员调整 Schema 可见性或必填规则。"
        />
      ) : null}
      <Summary data={data} />

      {data.latestSubmission !== null && data.latestSubmission.status !== "DRAFT" ? (
        <Alert
          type={
            presentAnnotationStatus(data.latestSubmission.status).tone === "error"
              ? "warning"
              : "info"
          }
          showIcon
          message={`最新提交 v${data.latestSubmission.submission_version}：${
            presentAnnotationStatus(data.latestSubmission.status).label
          }`}
          description={
            showForm
              ? "上一版本已终结，可基于返工要求创建新版本。"
              : "当前版本仍在流程中，工作台暂不允许创建新提交。"
          }
        />
      ) : null}

      {data.existingDraft !== null ? (
        <Card
          size="small"
          title="服务端已有草稿"
          extra={
            <Space wrap>
              <Button
                type="primary"
                loading={busyAction === "submit-draft"}
                disabled={!online || busyAction !== null}
                onClick={onSubmitDraft}
              >
                提交该版本
              </Button>
              <Button
                danger
                loading={busyAction === "withdraw-draft"}
                disabled={busyAction !== null}
                onClick={onWithdrawDraft}
              >
                撤回草稿
              </Button>
            </Space>
          }
        >
          <Space direction="vertical" size={10} className="ddp-stack">
            <Typography.Text type="secondary">
              服务端草稿内容不可原地编辑；如需重新填写，请先撤回。
            </Typography.Text>
            <RecordPreview data={data} />
          </Space>
        </Card>
      ) : null}

      {showForm ? (
        <Card title="标注表单" size="small">
          <Space direction="vertical" size={14} className="ddp-stack">
            <DraftNotice notice={draftNotice} />
            <Space wrap>
              <Typography.Text type="secondary">
                <SaveStateText state={saveState} updatedAt={draftUpdatedAt} />
              </Typography.Text>
              {!storageAvailable ? (
                <Tag color="orange">仅当前页面</Tag>
              ) : null}
              <Button size="small" onClick={onResetDraft} disabled={busyAction !== null}>
                清除本地草稿
              </Button>
            </Space>
            {disabledReason ? (
              <Alert type="info" showIcon message="当前不可提交" description={disabledReason} />
            ) : null}
            <Space wrap>
              <Typography.Text>数据来源</Typography.Text>
              <Select<DataSource>
                value={dataSource}
                options={DATA_SOURCE_VALUES.map((value) => ({
                  value,
                  label: value,
                }))}
                disabled={disabledReason !== null || busyAction !== null}
                onChange={onDataSourceChange}
                style={{ width: 160 }}
              />
              <Typography.Text type="secondary">
                必须如实标注；合成或模拟数据不得冒充 REAL。
              </Typography.Text>
            </Space>
            <Divider />
            {data.episodes.length === 0 ? (
              <PageState
                compact
                variant="empty"
                title="没有可标注的 Episode"
                description="批次与样本集均未提供 Episode；请检查批次 item_refs 或样本集快照。"
              />
            ) : (
              <Space direction="vertical" size={12} className="ddp-stack">
                {data.episodes.map((episode) => (
                  <EpisodeEditor
                    key={episode.episode_id}
                    projectId={projectId}
                    episode={episode}
                    definitions={definitions}
                    values={values[episode.episode_id] ?? {}}
                    errors={issuesForEpisode(episode.episode_id)}
                    disabled={disabledReason !== null || busyAction !== null}
                    onChange={(labelKey, value) =>
                      onFieldChange(episode.episode_id, labelKey, value)
                    }
                    aiSuggestions={data.aiSuggestions}
                  />
                ))}
              </Space>
            )}
            {recordIssues.length > 0 ? (
              <Alert
                type="error"
                showIcon
                message="记录级校验未通过"
                description={recordIssues.map((issue) => issue.message).join("；")}
              />
            ) : null}
            {validationAttempted && validationIssues.length > 0 ? (
              <Alert
                type="error"
                showIcon
                message={`还有 ${validationIssues.length} 项未完成`}
                description="请根据字段下方提示补齐必填、类型、范围或时间片段后再次提交。"
              />
            ) : null}
            <Space wrap>
              <Tooltip title={submitDisabledReason ?? ""}>
                <span>
                  <Button
                    type="primary"
                    loading={busyAction === "submit"}
                    disabled={
                      submitDisabledReason !== null ||
                      busyAction !== null ||
                      data.episodes.length === 0
                    }
                    onClick={onSubmit}
                  >
                    提交标注
                  </Button>
                </span>
              </Tooltip>
              <Button onClick={onBackToList}>返回任务详情</Button>
            </Space>
          </Space>
        </Card>
      ) : null}
    </Space>
  );
}
