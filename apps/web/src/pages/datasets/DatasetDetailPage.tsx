import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Divider, Space, Tabs, Tag, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/dataset/ActionButtons";
import DatasetGrantsSection from "../../components/dataset/DatasetGrantsSection";
import {
  DedupPoliciesSection,
  LeakagePoliciesSection,
  QualityPoliciesSection,
  SplitPoliciesSection,
} from "../../components/dataset/DatasetPoliciesSection";
import DatasetVersionsSection from "../../components/dataset/DatasetVersionsSection";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { datasetApi } from "../../services/datasets";
import {
  presentDatasetStatus,
  presentPurpose,
  presentUsagePolicy,
} from "../../services/datasetPresentation";
import { datasetListPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type { Dataset } from "../../types/dataset";

function DatasetDetailContent({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => datasetApi.get(projectId, datasetId, { signal }),
    [projectId, datasetId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载数据集"
        description="正在读取数据集、策略、版本与授权。"
      />
    );
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const dataset: Dataset = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，当前展示上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "code", label: "编码", children: dataset.code },
            {
              key: "status",
              label: "状态",
              children: <StatusTag value={dataset.status} presentation={presentDatasetStatus} />,
            },
            { key: "purpose", label: "用途", children: presentPurpose(dataset.purpose) },
            {
              key: "usage",
              label: "使用策略",
              children: <StatusTag value={dataset.usage_policy} presentation={presentUsagePolicy} />,
            },
            { key: "owner", label: "负责人", children: dataset.owner_id },
            {
              key: "lineage",
              label: "血缘线 ID",
              children: <Typography.Text copyable>{dataset.dataset_lineage_id}</Typography.Text>,
            },
            {
              key: "current",
              label: "当前版本",
              children: dataset.current_version_id ?? "—",
            },
            { key: "frozen", label: "冻结版本数", children: dataset.frozen_version_count },
            {
              key: "tags",
              label: "标签",
              children:
                dataset.tags.length > 0
                  ? dataset.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                  : "—",
            },
            {
              key: "updated",
              label: "更新时间",
              children: updatedAt ? new Date(updatedAt).toLocaleString() : dataset.updated_at,
            },
          ]}
        />
        <Space wrap>
          <Typography.Text strong>状态动作</Typography.Text>
          <ActionButtons
            actions={dataset.allowed_actions}
            onAction={async (action, payload) => {
              await datasetApi.act(projectId, datasetId, action, {
                version: dataset.version,
                reason: payload.reason,
              });
              refreshAll();
            }}
          />
        </Space>
        <Divider />
        <Tabs
          items={[
            {
              key: "policies",
              label: "策略",
              children: (
                <Space direction="vertical" size={20} style={{ width: "100%" }}>
                  <DedupPoliciesSection
                    key={`dedup:${refreshToken}`}
                    projectId={projectId}
                    datasetId={datasetId}
                  />
                  <SplitPoliciesSection
                    key={`split:${refreshToken}`}
                    projectId={projectId}
                    datasetId={datasetId}
                  />
                  <LeakagePoliciesSection
                    key={`leakage:${refreshToken}`}
                    projectId={projectId}
                    datasetId={datasetId}
                  />
                  <QualityPoliciesSection
                    key={`quality:${refreshToken}`}
                    projectId={projectId}
                    datasetId={datasetId}
                  />
                </Space>
              ),
            },
            {
              key: "versions",
              label: "版本",
              children: (
                <DatasetVersionsSection
                  key={`versions:${refreshToken}`}
                  projectId={projectId}
                  datasetId={datasetId}
                  onVersionChange={refreshAll}
                />
              ),
            },
            {
              key: "grants",
              label: "跨项目授权",
              children: (
                <DatasetGrantsSection
                  key={`grants:${refreshToken}`}
                  projectId={projectId}
                  datasetId={datasetId}
                />
              ),
            },
          ]}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={
        data ? (
          <Space wrap>
            <span>{data.name}</span>
            <StatusTag value={data.status} presentation={presentDatasetStatus} />
          </Space>
        ) : (
          "数据集详情"
        )
      }
      description={data ? `${data.code} · ${data.dataset_id}` : "查看数据集、策略、版本与授权。"}
      extra={
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(datasetListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refreshAll}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function DatasetDetailPage() {
  const projectId = useProjectId();
  const { datasetId } = useParams<{ datasetId: string }>();
  if (!projectId || !datasetId) {
    return (
      <PageContainer title="数据集详情" description="数据集必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据集详情" />
      </PageContainer>
    );
  }
  return (
    <DatasetDetailContent
      key={`${projectId}:${datasetId}`}
      projectId={projectId}
      datasetId={datasetId}
    />
  );
}
