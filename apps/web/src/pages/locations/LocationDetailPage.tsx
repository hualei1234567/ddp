import { ArrowLeftOutlined, EditOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import LocationEditModal from "../../components/locations/LocationEditModal";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/ui/StatusTag";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { formatDateTime, formatText } from "../../services/format";
import { presentLocationKind } from "../../services/locationPresentation";
import { locationApi } from "../../services/locations";
import { LOCATION_LIST_PATH } from "../../services/locationUrl";
import { resolvePagePhase } from "../../services/pagePhase";

function LocationDetailContent({
  projectId,
  locationId,
}: {
  projectId: string;
  locationId: string;
}) {
  const navigate = useNavigate();
  const [editOpen, setEditOpen] = useState(false);

  const load = useCallback(
    (signal: AbortSignal) =>
      locationApi.get(projectId, locationId, { signal }),
    [projectId, locationId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  let body;
  if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载场地" />;
  } else if (
    phase === "forbidden" ||
    phase === "not-found" ||
    phase === "error" ||
    data === null
  ) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "code", label: "编码", children: data.code },
            {
              key: "kind",
              label: "类型",
              children: (
                <StatusTag
                  value={data.kind}
                  presentation={presentLocationKind}
                />
              ),
            },
            { key: "address", label: "地址", children: formatText(data.address) },
            {
              key: "updated",
              label: "更新时间",
              children: formatDateTime(data.updated_at),
            },
            {
              key: "created",
              label: "创建时间",
              children: formatDateTime(data.created_at),
            },
            {
              key: "notes",
              label: "备注",
              children: formatText(data.notes),
            },
          ]}
        />
        <Button icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
          编辑场地
        </Button>
        <Typography.Text type="secondary">
          场地没有启用/停用状态，也不提供删除：它被设备与采集任务引用，
          随项目删除而级联清理。需要停用某个基地时请调整采集排期。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="场地详情"
      description="场地是组织维度；本页不做权威权限判断。"
      extra={
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(LOCATION_LIST_PATH)}
          >
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
      {data !== null ? (
        <LocationEditModal
          projectId={projectId}
          location={data}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={reload}
        />
      ) : null}
    </PageContainer>
  );
}

export default function LocationDetailPage() {
  const { locationId } = useParams<{ locationId: string }>();
  const projectId = useProjectId();

  if (!locationId) {
    return (
      <PageContainer title="场地详情" description="缺失场地标识。">
        <PageState
          variant="empty"
          title="未指定场地"
          description="请从场地列表进入详情页。"
        />
      </PageContainer>
    );
  }

  if (!projectId) {
    return (
      <PageContainer title="场地详情" description="场地必须按项目隔离展示。">
        <ProjectScopeRequired scope="场地详情" />
      </PageContainer>
    );
  }

  return (
    <LocationDetailContent
      key={`${projectId}:${locationId}`}
      projectId={projectId}
      locationId={locationId}
    />
  );
}
