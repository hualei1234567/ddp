import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Descriptions,
  Divider,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/dataset/ActionButtons";
import CountsGrid from "../../components/dataset/CountsGrid";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { datasetApi } from "../../services/datasets";
import {
  presentLeakageStatus,
  presentSplit,
  presentValidityStatus,
  presentVersionStatus,
} from "../../services/datasetPresentation";
import { datasetDetailPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  DatasetItem,
  DatasetVersion,
  LeakageReport,
  QualityReport,
  SelectionPreview,
} from "../../types/dataset";

function ItemsTable({
  projectId,
  datasetId,
  versionId,
}: {
  projectId: string;
  datasetId: string;
  versionId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listItems(projectId, datasetId, versionId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId, versionId],
  );
  const { items, loading, error, reload, hasMore, loadMore, loadingMore } = useCursorPage(
    load,
    (item) => item.dataset_item_id,
  );
  const columns: ColumnsType<DatasetItem> = [
    { title: "Episode", dataIndex: "episode_id", ellipsis: true },
    {
      title: "划分",
      dataIndex: "split",
      width: 90,
      render: (value: string) => <Tag>{presentSplit(value)}</Tag>,
    },
    { title: "角色", dataIndex: "sample_role", width: 120 },
    { title: "权重", dataIndex: "weight", width: 80 },
    { title: "条目 Hash", dataIndex: "item_sha256", ellipsis: true },
  ];
  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Table<DatasetItem>
        rowKey="dataset_item_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无条目（版本尚未构建或未生成）" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {hasMore ? (
        <Button block loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
    </Space>
  );
}

function PreviewPanel({
  projectId,
  datasetId,
  version,
}: {
  projectId: string;
  datasetId: string;
  version: DatasetVersion;
}) {
  const [preview, setPreview] = useState<SelectionPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const refs = {
    dedup: version.dedup_policy_ref,
    split: version.split_policy_ref,
    leakage: version.leakage_policy_ref,
    quality: version.quality_policy_ref,
  };
  const ready = refs.dedup && refs.split && refs.leakage;

  const run = async () => {
    if (!refs.dedup || !refs.split || !refs.leakage) {
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const result = await datasetApi.previewSelection(projectId, datasetId, version.dataset_version_id, {
        dedup_policy_id: refs.dedup.policy_id,
        dedup_policy_version: refs.dedup.version,
        split_policy_id: refs.split.policy_id,
        split_policy_version: refs.split.version,
        leakage_policy_id: refs.leakage.policy_id,
        leakage_policy_version: refs.leakage.version,
        quality_policy_id: refs.quality?.policy_id ?? null,
        quality_policy_version: refs.quality?.version ?? null,
        selection_filters: version.selection_filters,
      });
      setPreview(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "预览失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setPreview(null);
    setError(null);
  }, [version.dataset_version_id]);

  if (!ready) {
    return (
      <Typography.Text type="warning">
        版本未关联完整的去重/划分/泄漏策略，无法预览。
      </Typography.Text>
    );
  }

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Button size="small" loading={loading} onClick={() => void run()}>
        运行选择预览
      </Button>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      {preview ? (
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <CountsGrid
            items={[
              { label: "候选", value: preview.candidate_count },
              { label: "入选", value: preview.selected_count },
              { label: "排除", value: preview.excluded_count },
              { label: "去重", value: preview.duplicated_count },
            ]}
          />
          <Space wrap>
            {Object.entries(preview.split_counts).map(([key, value]) => (
              <Tag key={key}>
                {presentSplit(key.toUpperCase())}：{value}
              </Tag>
            ))}
            <Tag color={preview.split_preview_status === "OK" ? "green" : "orange"}>
              划分预览：{preview.split_preview_status}
            </Tag>
          </Space>
          {Object.keys(preview.exclusion_summary).length > 0 ? (
            <Space wrap>
              {Object.entries(preview.exclusion_summary).map(([key, value]) => (
                <Tag key={key} color="default">
                  {key}：{value}
                </Tag>
              ))}
            </Space>
          ) : null}
          {preview.warnings.map((warning) => (
            <Alert key={warning} type="warning" showIcon message={warning} />
          ))}
          {preview.blocking_issues.map((issue) => (
            <Alert key={issue} type="error" showIcon message={issue} />
          ))}
          <Typography.Text type="secondary">
            预览 Hash：{preview.preview_sha256}
          </Typography.Text>
        </Space>
      ) : null}
    </Space>
  );
}

function ReportsPanel({
  projectId,
  datasetId,
  versionId,
}: {
  projectId: string;
  datasetId: string;
  versionId: string;
}) {
  const loadQuality = useCallback(
    (signal: AbortSignal) =>
      datasetApi.getQualityReport(projectId, datasetId, versionId, { signal }),
    [projectId, datasetId, versionId],
  );
  const loadLeakage = useCallback(
    (signal: AbortSignal) =>
      datasetApi.getLeakageReport(projectId, datasetId, versionId, { signal }),
    [projectId, datasetId, versionId],
  );
  const quality = useRequest<QualityReport>(loadQuality);
  const leakage = useRequest<LeakageReport>(loadLeakage);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text strong>质量报告</Typography.Text>
        {quality.loading ? <PageState compact variant="loading" description="加载中" /> : null}
        {quality.error ? (
          <Typography.Text type="secondary">
            未生成或加载失败：{quality.error.message}
          </Typography.Text>
        ) : null}
        {quality.data ? (
          <>
            <CountsGrid
              items={[
                { label: "状态", value: quality.data.status },
                { label: "候选", value: quality.data.candidate_count },
                { label: "入选", value: quality.data.selected_count },
                { label: "排除", value: quality.data.excluded_count },
              ]}
            />
            {quality.data.blocking_issues.map((issue) => (
              <Alert key={issue} type="error" showIcon message={issue} />
            ))}
            <Typography.Text type="secondary">
              报告 Hash：{quality.data.report_sha256}
            </Typography.Text>
          </>
        ) : null}
      </Space>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text strong>泄漏报告</Typography.Text>
        {leakage.loading ? <PageState compact variant="loading" description="加载中" /> : null}
        {leakage.error ? (
          <Typography.Text type="secondary">
            未生成或加载失败：{leakage.error.message}
          </Typography.Text>
        ) : null}
        {leakage.data ? (
          <>
            <Space wrap>
              <StatusTag value={leakage.data.leakage_status} presentation={presentLeakageStatus} />
              <Tag>规则数：{leakage.data.checked_rule_count}</Tag>
              <Tag color={leakage.data.hard_violation_count > 0 ? "red" : "default"}>
                硬违规：{leakage.data.hard_violation_count}
              </Tag>
              <Tag color={leakage.data.soft_violation_count > 0 ? "orange" : "default"}>
                软违规：{leakage.data.soft_violation_count}
              </Tag>
            </Space>
            {leakage.data.violations.map((violation, index) => (
              <Alert
                key={`${violation.rule}:${index}`}
                type={violation.severity === "HARD" ? "error" : "warning"}
                showIcon
                message={`${violation.rule}（${violation.severity}）`}
                description={violation.detail ?? violation.item_id ?? undefined}
              />
            ))}
            <Typography.Text type="secondary">
              报告 Hash：{leakage.data.report_sha256}
            </Typography.Text>
          </>
        ) : null}
      </Space>
    </Space>
  );
}

function VersionDetailContent({
  projectId,
  datasetId,
  versionId,
}: {
  projectId: string;
  datasetId: string;
  versionId: string;
}) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => datasetApi.getVersion(projectId, datasetId, versionId, { signal }),
    [projectId, datasetId, versionId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载数据集版本" description="读取版本与报告。" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const version = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState compact variant="stale" description="刷新失败，展示上次成功数据。" onRetry={reload} />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "version", label: "版本", children: `v${version.dataset_version}` },
            {
              key: "status",
              label: "状态",
              children: <StatusTag value={version.status} presentation={presentVersionStatus} />,
            },
            {
              key: "validity",
              label: "有效性",
              children: <StatusTag value={version.validity_status} presentation={presentValidityStatus} />,
            },
            {
              key: "leakage",
              label: "泄漏状态",
              children: <StatusTag value={version.leakage_status} presentation={presentLeakageStatus} />,
            },
            { key: "attempt", label: "构建尝试", children: version.build_attempt },
            { key: "frozen", label: "冻结时间", children: version.frozen_at ?? "—" },
            {
              key: "manifest",
              label: "Manifest Hash",
              children: version.manifest_sha256 ?? "—",
            },
            {
              key: "supersedes",
              label: "替代版本",
              children: version.supersedes_version_id ?? "—",
            },
          ]}
        />
        <CountsGrid
          items={[
            { label: "候选", value: version.candidate_count },
            { label: "入选", value: version.selected_count },
            { label: "排除", value: version.excluded_count },
            { label: "去重", value: version.duplicated_count },
            { label: "条目", value: version.item_count },
            { label: "训练", value: version.train_count },
            { label: "验证", value: version.validation_count },
            { label: "测试", value: version.test_count },
          ]}
        />
        {version.blocking_issues.map((issue) => (
          <Alert key={issue} type="error" showIcon message={issue} />
        ))}
        {version.warnings.map((warning) => (
          <Alert key={warning} type="warning" showIcon message={warning} />
        ))}
        <Space wrap>
          <Typography.Text strong>版本动作</Typography.Text>
          <ActionButtons
            actions={version.allowed_actions}
            onAction={async (action, payload) => {
              await datasetApi.actVersion(projectId, datasetId, versionId, action, {
                version: version.version,
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
              key: "preview",
              label: "选择预览",
              children: (
                <PreviewPanel
                  key={`preview:${refreshToken}`}
                  projectId={projectId}
                  datasetId={datasetId}
                  version={version}
                />
              ),
            },
            {
              key: "items",
              label: "条目",
              children: (
                <ItemsTable
                  key={`items:${refreshToken}`}
                  projectId={projectId}
                  datasetId={datasetId}
                  versionId={versionId}
                />
              ),
            },
            {
              key: "reports",
              label: "质量与泄漏报告",
              children: (
                <ReportsPanel
                  key={`reports:${refreshToken}`}
                  projectId={projectId}
                  datasetId={datasetId}
                  versionId={versionId}
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
      title={data ? `数据集版本 v${data.dataset_version}` : "数据集版本"}
      description={data ? `${data.dataset_version_id}` : "查看版本状态、报告与条目。"}
      extra={
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(datasetDetailPath(datasetId))}>
            返回数据集
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

export default function DatasetVersionDetailPage() {
  const projectId = useProjectId();
  const { datasetId, versionId } = useParams<{ datasetId: string; versionId: string }>();
  if (!projectId || !datasetId || !versionId) {
    return (
      <PageContainer title="数据集版本" description="版本必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据集版本" />
      </PageContainer>
    );
  }
  return (
    <VersionDetailContent
      key={`${projectId}:${datasetId}:${versionId}`}
      projectId={projectId}
      datasetId={datasetId}
      versionId={versionId}
    />
  );
}
