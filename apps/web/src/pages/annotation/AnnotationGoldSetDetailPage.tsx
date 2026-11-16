import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import GoldAnswerSection from "../../components/annotation/GoldAnswerSection";
import GoldSetActionBar from "../../components/quality/GoldSetActionBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { ApiError } from "../../services/api";
import { annotationApi } from "../../services/annotation";
import { buildAnnotationGoldSetListPath } from "../../services/annotationUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import { GOLD_SET_ACTIONS } from "../../services/qualityPresentation";
import type { GoldSet, GoldSetItem } from "../../types/annotation";

function itemColumns(): TableColumnsType<GoldSetItem> {
  return [
    {
      title: "黄金项",
      dataIndex: "gold_item_id",
      width: 220,
      render: (value: string) => <IdentifierValue value={value} label="黄金项 ID" />,
    },
    {
      title: "Episode",
      dataIndex: "episode_id",
      width: 220,
      render: (value: string) => <IdentifierValue value={value} label="Episode ID" />,
    },
    { title: "难度", dataIndex: "difficulty", width: 100 },
    { title: "权重", dataIndex: "weight", width: 80 },
    {
      title: "启用",
      dataIndex: "active",
      width: 80,
      render: (value: boolean) => (value ? "是" : "否"),
    },
    {
      title: "答案状态",
      dataIndex: "answers_redacted",
      width: 140,
      render: (value: boolean) => (
        <Tag color={value === false ? "red" : "default"}>
          {value ? "已脱敏" : "已授权展示"}
        </Tag>
      ),
    },
  ];
}

function GoldSetDetailContent({
  projectId,
  goldSetId,
}: {
  projectId: string;
  goldSetId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getGoldSet(projectId, goldSetId, { signal }),
    [projectId, goldSetId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const [busyAction, setBusyAction] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [answers, setAnswers] = useState<GoldSet | null>(null);
  const [answerError, setAnswerError] = useState<ApiError | null>(null);
  const [answerLoading, setAnswerLoading] = useState(false);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const loadAnswers = useCallback(async () => {
    setAnswerLoading(true);
    setAnswerError(null);
    setAnswers(null);
    try {
      setAnswers(await annotationApi.getGoldSetAnswers(projectId, goldSetId));
    } catch (caught) {
      setAnswerError(
        caught instanceof ApiError
          ? caught
          : new ApiError({
              kind: "network",
              message: "黄金集答案请求失败。",
              cause: caught,
            }),
      );
    } finally {
      setAnswerLoading(false);
    }
  }, [goldSetId, projectId]);

  const handleAction = useCallback(
    async (action: string, reason: string | null) => {
      setBusyAction(true);
      setActionError(null);
      try {
        const version = data?.version;
        if (version === undefined) {
          throw new Error("缺少黄金集版本号，无法执行动作，请刷新后重试。");
        }
        await annotationApi.actOnGoldSet(projectId, goldSetId, action, {
          version,
          reason,
        });
        reload();
      } catch (caught) {
        const apiError =
          caught instanceof ApiError
            ? caught
            : new ApiError({
                kind: "network",
                message: "动作请求失败，请稍后重试。",
                cause: caught,
              });
        setActionError(apiError);
        throw apiError;
      } finally {
        setBusyAction(false);
      }
    },
    [data?.version, goldSetId, projectId, reload],
  );

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate(buildAnnotationGoldSetListPath(projectId))}
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
    body = <PageState variant="loading" title="正在加载黄金集详情" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的黄金集。"
            onRetry={reload}
          />
        ) : null}
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>状态动作</Typography.Text>
          <GoldSetActionBar
            actions={GOLD_SET_ACTIONS}
            busy={busyAction}
            onAction={handleAction}
          />
          <Typography.Text type="secondary">
            动作集合与状态迁移以服务端为准：非法迁移返回 409/422，页面不会提前隐藏按钮。
          </Typography.Text>
          {actionError ? (
            <ApiErrorState error={actionError} compact />
          ) : null}
        </Space>
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="黄金集 ID">
            <IdentifierValue value={data.gold_set_id} label="黄金集 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="编码">{data.code}</Descriptions.Item>
          <Descriptions.Item label="名称">{data.name ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <AnnotationStatusTag value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="Schema">
            {data.schema_id} · v{data.schema_version}
          </Descriptions.Item>
          <Descriptions.Item label="盲测">{data.blind ? "是" : "否"}</Descriptions.Item>
          <Descriptions.Item label="曝光策略">
            {data.exposure_policy}
          </Descriptions.Item>
          <Descriptions.Item label="每人曝光上限">
            {data.max_exposure_per_annotator}
          </Descriptions.Item>
          <Descriptions.Item label="轮换策略">
            {data.rotation_policy}
          </Descriptions.Item>
          <Descriptions.Item label="Schema Hash">
            <Typography.Text code>{data.schema_sha256}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
        <Table<GoldSetItem>
          rowKey="gold_item_id"
          size="small"
          columns={itemColumns()}
          dataSource={data.items}
          pagination={false}
          scroll={{ x: 900 }}
        />
        <Space direction="vertical" size={8} className="ddp-stack">
          <GoldAnswerSection
            answers={answers}
            error={answerError}
            loading={answerLoading}
            onLoad={() => void loadAnswers()}
          />
        </Space>
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? `黄金集 · ${data.code}` : "黄金集详情"}
      description="默认脱敏视图；期望答案必须显式请求，403 会展示为 Forbidden。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationGoldSetDetailPage() {
  const projectId = useProjectId();
  const { goldSetId } = useParams<{ goldSetId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="黄金集详情" description="黄金集必须按项目隔离展示。">
        <ProjectScopeRequired scope="黄金集详情" />
      </PageContainer>
    );
  }
  if (!goldSetId) {
    return (
      <PageContainer title="黄金集详情">
        <ProjectScopeRequired scope="黄金集详情" />
      </PageContainer>
    );
  }
  return (
    <GoldSetDetailContent
      key={`${projectId}:${goldSetId}`}
      projectId={projectId}
      goldSetId={goldSetId}
    />
  );
}
