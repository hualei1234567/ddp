import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Tabs,
  Typography,
} from "antd";
import type { ReactNode } from "react";
import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { devicesApi } from "../../services/devices";
import { resolveDevicePagePhase } from "../../services/devicePageState";
import { getDeviceType } from "../../services/devicePresentation";
import {
  formatCapabilityProfile,
  formatDateTime,
  formatIdentifier,
  formatText,
  formatTimestamp,
} from "../../services/format";
import { buildProjectQuery } from "../../services/project";
import type { Device } from "../../types/device";
import CalibrationList from "./CalibrationList";
import SensorList from "./SensorList";

function DeviceOverview({ device }: { device: Device }) {
  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      <Alert
        type="info"
        showIcon
        message="状态以服务端为准"
        description="本页只展示设备档案与标定记录；状态变更、维修与退役动作将在设备写入流程接入后开放。"
      />
      <Descriptions
        column={{ xs: 1, sm: 1, md: 2 }}
        size="middle"
        bordered
      >
        <Descriptions.Item label="设备名称">
          {device.name}
        </Descriptions.Item>
        <Descriptions.Item label="设备编码">
          {device.code}
        </Descriptions.Item>
        <Descriptions.Item label="设备 ID">
          <Typography.Text code copyable>
            {device.device_id}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <StatusTag kind="device" value={device.status} />
        </Descriptions.Item>
        <Descriptions.Item label="设备类型">
          {getDeviceType(device.device_type).label}
        </Descriptions.Item>
        <Descriptions.Item label="厂商 / 型号">
          {formatText(
            [device.vendor, device.model]
              .filter((value): value is string => Boolean(value))
              .join(" / "),
          )}
        </Descriptions.Item>
        <Descriptions.Item label="序列号">
          {formatText(device.serial_number)}
        </Descriptions.Item>
        <Descriptions.Item label="固件 / 硬件版本">
          {formatText(
            [device.firmware_version, device.hardware_version]
              .filter((value): value is string => Boolean(value))
              .join(" / "),
          )}
        </Descriptions.Item>
        <Descriptions.Item label="挂载机器人">
          {formatIdentifier(device.robot_id)}
        </Descriptions.Item>
        <Descriptions.Item label="位置">
          {formatIdentifier(device.location_id)}
        </Descriptions.Item>
        <Descriptions.Item label="负责人">
          {formatIdentifier(device.owner_user_id)}
        </Descriptions.Item>
        <Descriptions.Item label="标定配置">
          {formatIdentifier(device.calibration_profile_id)}
        </Descriptions.Item>
        <Descriptions.Item label="能力描述">
          <Typography.Text
            ellipsis={{ tooltip: formatCapabilityProfile(device.capability_profile) }}
          >
            {formatCapabilityProfile(device.capability_profile)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="档案版本">
          v{device.version}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {formatDateTime(device.created_at)}
        </Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {formatDateTime(device.updated_at)}
        </Descriptions.Item>
      </Descriptions>
    </Space>
  );
}

function DeviceDetailContent({
  projectId,
  deviceId,
}: {
  projectId: string;
  deviceId: string;
}) {
  const navigate = useNavigate();
  const projectSearch = buildProjectQuery(projectId);

  const loadDevice = useCallback(
    (signal: AbortSignal) => devicesApi.getDevice(projectId, deviceId, { signal }),
    [projectId, deviceId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadDevice);

  const phase = resolveDevicePagePhase({
    hasData: data !== null,
    loading,
    error,
  });
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={reload} />
  ) : null;

  let body: ReactNode;
  if (phase === "forbidden") {
    // 权限被拒时整页 Forbidden，不展示缓存的设备敏感信息。
    body = errorBody;
  } else if (phase === "not-found") {
    body = (
      <PageState
        variant="empty"
        title="设备不存在或无权查看"
        description="服务端未找到该设备；该设备可能属于其他项目，或已被移除。"
        action={
          <Button type="primary" onClick={() => navigate(`/devices${projectSearch}`)}>
            返回设备列表
          </Button>
        }
      />
    );
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载设备详情"
        description="正在读取设备档案、传感器与标定记录。"
      />
    );
  } else if (phase === "error") {
    body = errorBody;
  } else if (data) {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState variant="stale" compact onRetry={reload} />
        ) : null}
        <Card>
          <Tabs
            defaultActiveKey="overview"
            items={[
              {
                key: "overview",
                label: "概览",
                children: <DeviceOverview device={data} />,
              },
              {
                key: "sensors",
                label: "传感器",
                children: (
                  <SensorList projectId={projectId} deviceId={deviceId} />
                ),
              },
              {
                key: "calibrations",
                label: "标定记录",
                children: (
                  <CalibrationList
                    projectId={projectId}
                    deviceId={deviceId}
                  />
                ),
              },
            ]}
          />
        </Card>
      </Space>
    );
  } else {
    body = <PageState variant="empty" title="暂无设备详情" />;
  }

  const title = data ? (
    <Space wrap>
      <span>{data.name}</span>
      <StatusTag kind="device" value={data.status} />
    </Space>
  ) : (
    "设备详情"
  );

  const description = data
    ? `${data.code} · 设备 ID：${data.device_id}`
    : "查看设备档案、传感器与标定记录。敏感字段与权限以服务端返回为准。";

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Link to={`/devices${projectSearch}`}>
        <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
      </Link>
      <Button
        icon={<ReloadOutlined />}
        loading={refreshing}
        onClick={reload}
      >
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer
      title={title}
      description={description}
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function DeviceDetailPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const { deviceId } = useParams<{ deviceId: string }>();

  if (!projectId) {
    return (
      <PageContainer
        title="设备详情"
        description="设备数据必须按项目隔离展示。"
      >
        <PageState
          variant="empty"
          title="未选择项目"
          description="请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。前端不会使用硬编码的默认项目。"
          action={
            <Button type="primary" onClick={() => navigate("/")}>
              返回工作台
            </Button>
          }
        />
      </PageContainer>
    );
  }

  if (!deviceId) {
    return (
      <PageContainer title="设备详情">
        <PageState
          variant="empty"
          title="设备 ID 缺失"
          description="当前地址缺少设备 ID，请从设备列表重新进入。"
          action={
            <Button
              type="primary"
              onClick={() => navigate(`/devices${buildProjectQuery(projectId)}`)}
            >
              返回设备列表
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <DeviceDetailContent
      key={`${projectId}:${deviceId}`}
      projectId={projectId}
      deviceId={deviceId}
    />
  );
}
