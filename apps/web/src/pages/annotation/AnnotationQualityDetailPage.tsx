import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Tag, Typography } from "antd";
import { useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import {
  buildAnnotationQualityListPath,
  buildAnnotationSubmissionPath,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";

function QualityDetailContent({
  projectId,
  qualitySampleId,
}: {
  projectId: string;
  qualitySampleId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getQualitySample(projectId, qualitySampleId, { signal }),
    [projectId, qualitySampleId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() =>
          navigate(
            data
              ? buildAnnotationQualityListPath(projectId, data.task_id)
              : buildAnnotationQualityListPath(projectId),
          )
        }
      >
        返回列表
      </Button>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载抽检详情" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的抽检详情。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="抽检 ID">
            <IdentifierValue value={data.quality_sample_id} label="抽检 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="任务">
            <IdentifierValue value={data.task_id} label="任务 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <AnnotationStatusTag value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="策略版本">
            {data.sampling_policy_version}
          </Descriptions.Item>
          <Descriptions.Item label="抽样类型">{data.sample_type}</Descriptions.Item>
          <Descriptions.Item label="分层">{data.stratum ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="标注人">
            <IdentifierValue value={data.annotator_id} label="标注人 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="复核人">
            <IdentifierValue value={data.reviewer_id} label="复核人 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="结论">
            {data.outcome ?? "Pending"}
          </Descriptions.Item>
          <Descriptions.Item label="提交">
            <Button
              type="link"
              onClick={() =>
                navigate(
                  buildAnnotationSubmissionPath(projectId, data.submission_id),
                )
              }
            >
              查看提交
            </Button>
          </Descriptions.Item>
          <Descriptions.Item label="截止时间">
            {formatDateTime(data.due_at)}
          </Descriptions.Item>
          <Descriptions.Item label="关闭时间">
            {formatDateTime(data.closed_at)}
          </Descriptions.Item>
        </Descriptions>
        <Space direction="vertical" size={4}>
          <Typography.Text strong>抽样理由</Typography.Text>
          <Typography.Paragraph>{data.sample_reason}</Typography.Paragraph>
        </Space>
        {data.reason ? (
          <Space direction="vertical" size={4}>
            <Typography.Text strong>结论原因</Typography.Text>
            <Typography.Paragraph>{data.reason}</Typography.Paragraph>
          </Space>
        ) : null}
        <Space wrap>
          <Tag>抽检项 {data.item_refs.length}</Tag>
          {data.review_id ? <Tag>复核 {data.review_id}</Tag> : null}
          {data.arbitration_id ? <Tag>仲裁 {data.arbitration_id}</Tag> : null}
        </Space>
        <Typography.Text type="secondary">
          {data.outcome === null && data.status !== "CLOSED"
            ? "抽检结论尚未完成，不默认通过。"
            : "结论与策略版本以服务端记录为准。"}
        </Typography.Text>
        <Typography.Text code>{JSON.stringify(data.metadata)}</Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="质检抽检详情"
      description="只读查看抽样策略、复核人与闭环结论。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationQualityDetailPage() {
  const projectId = useProjectId();
  const { qualitySampleId } = useParams<{ qualitySampleId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="质检抽检详情" description="抽检必须按项目隔离展示。">
        <ProjectScopeRequired scope="质检抽检详情" />
      </PageContainer>
    );
  }
  if (!qualitySampleId) {
    return (
      <PageContainer title="质检抽检详情">
        <ProjectScopeRequired scope="质检抽检详情" />
      </PageContainer>
    );
  }
  return (
    <QualityDetailContent
      key={`${projectId}:${qualitySampleId}`}
      projectId={projectId}
      qualitySampleId={qualitySampleId}
    />
  );
}
