import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Space,
  Typography,
} from "antd";
import type { ReactNode } from "react";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import DeviceActionBar from "../../components/devices/DeviceActionBar";
import RobotFormModal from "../../components/devices/RobotFormModal";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import {
  devicesApi,
  type DeviceAction,
  type DeviceActionPayload,
  type RobotRecord,
} from "../../services/devices";
import { resolvePagePhase } from "../../services/pagePhase";
import type { PagePhase } from "../../services/pagePhase";
import {
  formatCapabilityProfile,
  formatDateTime,
  formatText,
  formatTimestamp,
} from "../../services/format";

export interface RobotDetailBodyProps {
  projectId: string;
  data: RobotRecord | null;
  phase: PagePhase;
  error: ApiError | null;
  stale: boolean;
  /** 生命周期动作区；由页面注入，方便静态渲染测试。 */
  actionsNode?: ReactNode;
  onRetry: () => void;
  onBackToList: () => void;
}

/** 机器人详情主体；与请求状态解耦，便于覆盖 403/404/空态/Stale。 */
export function RobotDetailBody({
  projectId,
  data,
  phase,
  error,
  stale,
  actionsNode,
  onRetry,
  onBackToList,
}: RobotDetailBodyProps) {
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;

  if (phase === "forbidden") {
    return <>{errorBody}</>;
  }
  if (phase === "not-found") {
    return (
      <PageState
        variant="empty"
        title="机器人不存在或无权查看"
        description="服务端未找到该机器人；跨项目资源统一不可见。"
        action={<Button onClick={onBackToList}>返回机器人列表</Button>}
      />
    );
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载机器人详情"
        description="正在读取机器人档案与生命周期动作。"
      />
    );
  }
  if (phase === "error") {
    return <>{errorBody}</>;
  }
  if (!data) {
    return <PageState variant="empty" title="暂无机器人详情" />;
  }

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的机器人数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Card title="状态动作">{actionsNode}</Card>
      <Card>
        <Descriptions column={{ xs: 1, sm: 1, md: 2 }} size="middle" bordered>
          <Descriptions.Item label="名称">{data.name}</Descriptions.Item>
          <Descriptions.Item label="编码">{data.code}</Descriptions.Item>
          <Descriptions.Item label="机器人 ID">
            <IdentifierValue value={data.robot_id} label="机器人 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusTag kind="device" value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="类型">
            {formatText(data.robot_type)}
          </Descriptions.Item>
          <Descriptions.Item label="厂商 / 型号">
            {formatText(
              [data.vendor, data.model]
                .filter((value): value is string => Boolean(value))
                .join(" / "),
            )}
          </Descriptions.Item>
          <Descriptions.Item label="序列号">
            {formatText(data.serial_number)}
          </Descriptions.Item>
          <Descriptions.Item label="固件 / 硬件版本">
            {formatText(
              [data.firmware_version, data.hardware_version]
                .filter((value): value is string => Boolean(value))
                .join(" / "),
            )}
          </Descriptions.Item>
          <Descriptions.Item label="位置">
            <IdentifierValue value={data.location_id} label="位置 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="负责人">
            <IdentifierValue value={data.owner_user_id} label="负责人 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="标定配置">
            <IdentifierValue
              value={data.calibration_profile_id}
              label="标定配置 ID"
            />
          </Descriptions.Item>
          <Descriptions.Item label="能力描述">
            <Typography.Text
              ellipsis={{ tooltip: formatCapabilityProfile(data.capability_profile) }}
            >
              {formatCapabilityProfile(data.capability_profile)}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="状态原因">
            {formatText(data.status_reason)}
          </Descriptions.Item>
          <Descriptions.Item label="故障码">
            {formatText(data.fault_code)}
          </Descriptions.Item>
          <Descriptions.Item label="故障详情">
            {formatText(data.fault_detail)}
          </Descriptions.Item>
          <Descriptions.Item label="故障时间">
            {formatDateTime(data.fault_occurred_at)}
          </Descriptions.Item>
          <Descriptions.Item label="状态变更时间">
            {formatDateTime(data.status_changed_at)}
          </Descriptions.Item>
          <Descriptions.Item label="档案版本">
            v{data.version}
          </Descriptions.Item>
        </Descriptions>
        <Space style={{ marginTop: 16 }}>
          <Link to={`/robots?project_id=${encodeURIComponent(projectId)}`}>
            <Button icon={<ArrowLeftOutlined />}>返回机器人列表</Button>
          </Link>
        </Space>
      </Card>
    </Space>
  );
}

function RobotDetailContent({
  projectId,
  robotId,
}: {
  projectId: string;
  robotId: string;
}) {
  const navigate = useNavigate();
  const [editOpen, setEditOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const loadRobot = useCallback(
    (signal: AbortSignal) => devicesApi.getRobot(projectId, robotId, { signal }),
    [projectId, robotId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadRobot);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const handleAction = useCallback(
    async (action: DeviceAction, payload: DeviceActionPayload) => {
      if (!data) {
        return;
      }
      setBusyAction(action);
      try {
        await devicesApi.actOnRobot(projectId, data.robot_id, action, payload, {
          version: data.version,
        });
        reload();
      } finally {
        setBusyAction(null);
      }
    },
    [data, projectId, reload],
  );

  const title = data ? (
    <Space wrap>
      <span>{data.name}</span>
      <StatusTag kind="device" value={data.status} />
    </Space>
  ) : (
    "机器人详情"
  );
  const description = data ? (
    <Space wrap size={4}>
      <span>{data.code}</span>
      <span>· 类型：{data.robot_type}</span>
      <span>· 更新于 {formatTimestamp(updatedAt)}</span>
    </Space>
  ) : (
    "查看机器人档案、状态原因与生命周期动作；动作合法性以服务端为准。"
  );

  const extra = (
    <Space wrap>
      <Button
        onClick={() => setEditOpen(true)}
        disabled={!data || Boolean(busyAction)}
      >
        编辑机器人
      </Button>
      <Link to={`/robots?project_id=${encodeURIComponent(projectId)}`}>
        <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
      </Link>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  return (
    <>
      <PageContainer title={title} description={description} extra={extra}>
        <RobotDetailBody
          projectId={projectId}
          data={data}
          phase={phase}
          error={error}
          stale={stale}
          actionsNode={
            data ? (
              <DeviceActionBar
                allowedActions={data.allowed_actions}
                entityLabel="机器人"
                busy={Boolean(busyAction)}
                onAction={handleAction}
              />
            ) : null
          }
          onRetry={reload}
          onBackToList={() =>
            navigate(`/robots?project_id=${encodeURIComponent(projectId)}`)
          }
        />
      </PageContainer>
      <RobotFormModal
        projectId={projectId}
        mode="edit"
        robot={data}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onSaved={() => reload()}
      />
    </>
  );
}

export default function RobotDetailPage() {
  const projectId = useProjectId();
  const { robotId } = useParams<{ robotId: string }>();

  if (!projectId) {
    return (
      <PageContainer title="机器人详情" description="机器人必须按项目隔离展示。">
        <ProjectScopeRequired scope="机器人详情" />
      </PageContainer>
    );
  }

  if (!robotId) {
    return (
      <PageContainer title="机器人详情">
        <PageState
          variant="empty"
          title="机器人 ID 缺失"
          description="当前地址缺少机器人 ID，请从机器人列表重新进入。"
        />
      </PageContainer>
    );
  }

  return (
    <RobotDetailContent
      key={`${projectId}:${robotId}`}
      projectId={projectId}
      robotId={robotId}
    />
  );
}
