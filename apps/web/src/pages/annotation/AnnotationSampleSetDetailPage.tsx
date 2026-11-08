import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
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
  buildAnnotationSampleSetListPath,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { SampleEpisodeRef } from "../../types/annotation";

function episodeColumns(): TableColumnsType<SampleEpisodeRef> {
  return [
    {
      title: "Episode",
      dataIndex: "episode_id",
      width: 220,
      render: (value: string) => <IdentifierValue value={value} label="Episode ID" />,
    },
    {
      title: "谱系 / 版本",
      key: "lineage",
      width: 220,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <IdentifierValue value={item.episode_lineage_id} label="谱系 ID" />
          <span>v{item.episode_version}</span>
        </Space>
      ),
    },
    { title: "角色", dataIndex: "sample_role", width: 100 },
    { title: "分层", dataIndex: "stratum", width: 120 },
    {
      title: "时间范围",
      key: "time",
      width: 220,
      render: (_value, item) =>
        item.start_time_ns === null || item.end_time_ns === null
          ? "整段"
          : `${item.start_time_ns} – ${item.end_time_ns} ns`,
    },
    {
      title: "入选理由",
      dataIndex: "selected_reason",
      render: (value: string) => value,
    },
  ];
}

function SampleSetDetailContent({
  projectId,
  sampleSetId,
}: {
  projectId: string;
  sampleSetId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getSampleSet(projectId, sampleSetId, { signal }),
    [projectId, sampleSetId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate(buildAnnotationSampleSetListPath(projectId))}
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
    body = <PageState variant="loading" title="正在加载样本集详情" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的样本集。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="样本集 ID">
            <IdentifierValue value={data.sample_set_id} label="样本集 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="编码">{data.code}</Descriptions.Item>
          <Descriptions.Item label="名称">{data.name ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <AnnotationStatusTag value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="策略">{data.strategy}</Descriptions.Item>
          <Descriptions.Item label="去重键">{data.dedup_key}</Descriptions.Item>
          <Descriptions.Item label="选择规则版本">
            {data.selection_rule_version}
          </Descriptions.Item>
          <Descriptions.Item label="数据来源">{data.data_source}</Descriptions.Item>
          <Descriptions.Item label="候选 / 入选">
            {data.candidate_count} / {data.selected_count}
          </Descriptions.Item>
          <Descriptions.Item label="排除 / 去重">
            {data.excluded_count} / {data.deduplicated_count}
          </Descriptions.Item>
          <Descriptions.Item label="快照时间">
            {formatDateTime(data.snapshot_at)}
          </Descriptions.Item>
          <Descriptions.Item label="快照 Hash">
            <Typography.Text code>{data.snapshot_sha256 ?? "未快照"}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="取消原因">
            {data.cancelled_reason ?? "—"}
          </Descriptions.Item>
        </Descriptions>
        <Space wrap>
          <Tag>过滤条件</Tag>
          <Typography.Text code>{JSON.stringify(data.filters)}</Typography.Text>
        </Space>
        <Space wrap>
          <Tag>分层</Tag>
          <Typography.Text code>{JSON.stringify(data.strata)}</Typography.Text>
        </Space>
        <Space wrap>
          <Tag>排除汇总</Tag>
          <Typography.Text code>{JSON.stringify(data.exclusion_summary)}</Typography.Text>
        </Space>
        <Typography.Text type="secondary">
          样本集不是 DatasetVersion；阶段 3 不执行正式数据集冻结、划分或训练导出。
        </Typography.Text>
        <Table<SampleEpisodeRef>
          rowKey="episode_id"
          size="small"
          columns={episodeColumns()}
          dataSource={data.episode_refs}
          pagination={false}
          scroll={{ x: 1000 }}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? `样本集 · ${data.code}` : "标注样本集详情"}
      description="只读查看样本快照、入选理由与排除统计。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationSampleSetDetailPage() {
  const projectId = useProjectId();
  const { sampleSetId } = useParams<{ sampleSetId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="标注样本集详情" description="样本集必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注样本集详情" />
      </PageContainer>
    );
  }
  if (!sampleSetId) {
    return (
      <PageContainer title="标注样本集详情">
        <ProjectScopeRequired scope="标注样本集详情" />
      </PageContainer>
    );
  }
  return (
    <SampleSetDetailContent
      key={`${projectId}:${sampleSetId}`}
      projectId={projectId}
      sampleSetId={sampleSetId}
    />
  );
}
