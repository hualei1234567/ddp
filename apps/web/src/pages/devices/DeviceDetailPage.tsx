import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { ReactNode } from "react";
import { useCallback, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import DeviceActionBar from "../../components/devices/DeviceActionBar";
import DeviceFormModal from "../../components/devices/DeviceFormModal";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import {
  DEFAULT_DEVICE_DETAIL_TAB,
  buildDeviceSearch,
  readDeviceListFilters,
  resolveDeviceDetailTab,
} from "../../services/deviceListUrl";
import type { DeviceDetailTab } from "../../services/deviceListUrl";
import { devicesApi } from "../../services/devices";
import type { DeviceAction, DeviceActionPayload } from "../../services/devices";
import { evaluateDeviceFreshness } from "../../services/deviceFreshness";
import type {
  CalibrationExpirySummary,
  DeviceFreshness,
} from "../../services/deviceFreshness";
import { resolveDevicePagePhase } from "../../services/devicePageState";
import type { DevicePagePhase } from "../../services/devicePageState";
import { getDeviceType } from "../../services/devicePresentation";
import {
  formatCapabilityProfile,
  formatDateTime,
  formatText,
  formatTimestamp,
} from "../../services/format";
import type { Device } from "../../types/device";
import CalibrationList from "./CalibrationList";
import SensorList from "./SensorList";

function freshnessLabel(freshness: DeviceFreshness): string {
  if (freshness.level === "fresh") {
    return "正常（服务端判定）";
  }
  if (freshness.level === "stale") {
    return freshness.lastHeartbeatAt
      ? `可能过期（最近心跳 ${formatDateTime(freshness.lastHeartbeatAt)}）`
      : "可能过期（已超过服务端新鲜度阈值）";
  }
  return "未知（服务端未下发新鲜度阈值）";
}

function DeviceOverview({
  device,
  freshness,
}: {
  device: Device;
  freshness: DeviceFreshness;
}) {
  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      <Alert
        type="info"
        showIcon
        message="状态以服务端为准"
        description="本页展示设备档案、状态动作与标定记录。启用前服务端会校验必需的标定是否生效；非法状态迁移与版本冲突会被服务端拒绝。"
      />
      <Descriptions column={{ xs: 1, sm: 1, md: 2 }} size="middle" bordered>
        <Descriptions.Item label="设备名称">
          {device.name}
        </Descriptions.Item>
        <Descriptions.Item label="设备编码">
          {device.code}
        </Descriptions.Item>
        <Descriptions.Item label="设备 ID">
          <IdentifierValue value={device.device_id} label="设备 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Space wrap>
            <StatusTag
              kind="device"
              value={device.status}
              stale={freshness.level === "stale"}
              staleHint={
                freshness.lastHeartbeatAt
                  ? `最近心跳为 ${formatDateTime(freshness.lastHeartbeatAt)}`
                  : undefined
              }
            />
            {freshness.level === "unknown" ? (
              <Tag>新鲜度未知</Tag>
            ) : null}
          </Space>
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
          <IdentifierValue
            value={device.robot_id}
            label="机器人 ID"
            to={
              device.robot_id
                ? {
                    pathname: `/robots/${encodeURIComponent(device.robot_id)}`,
                    search: `?project_id=${encodeURIComponent(device.project_id)}`,
                  }
                : undefined
            }
          />
        </Descriptions.Item>
        <Descriptions.Item label="位置">
          <IdentifierValue value={device.location_id} label="位置 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="负责人">
          <IdentifierValue value={device.owner_user_id} label="负责人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="标定配置">
          <IdentifierValue
            value={device.calibration_profile_id}
            label="标定配置 ID"
          />
        </Descriptions.Item>
        <Descriptions.Item label="能力描述">
          <Typography.Text
            ellipsis={{ tooltip: formatCapabilityProfile(device.capability_profile) }}
          >
            {formatCapabilityProfile(device.capability_profile)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="状态新鲜度">
          <Typography.Text
            type={freshness.level === "stale" ? "danger" : "secondary"}
          >
            {freshnessLabel(freshness)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="最近心跳">
          {formatDateTime(device.last_heartbeat_at ?? null)}
        </Descriptions.Item>
        <Descriptions.Item label="最近同步">
          {formatDateTime(device.last_synced_at ?? null)}
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

export interface DeviceDetailBodyProps {
  projectId: string;
  deviceId: string;
  data: Device | null;
  phase: DevicePagePhase;
  error: ApiError | null;
  /** 最近一次刷新失败但保留了旧数据。 */
  stale: boolean;
  activeTab: DeviceDetailTab;
  listSearch: string;
  tabsRefreshToken: number;
  /** 标定 Tab 回报的过期摘要；未加载时为 null。 */
  calibrationExpiry?: CalibrationExpirySummary | null;
  /** 状态动作区；由页面注入，保持本组件可静态渲染。 */
  actionsNode?: ReactNode;
  onTabChange: (key: string) => void;
  onExpiryChange?: (summary: CalibrationExpirySummary) => void;
  onRetry: () => void;
  onBackToList: () => void;
  now?: number;
}

/**
 * 设备详情主体。与请求状态解耦，便于覆盖 403/404/空态/Stale 与 Tab 跳转。
 */
export function DeviceDetailBody({
  projectId,
  deviceId,
  data,
  phase,
  error,
  stale,
  activeTab,
  listSearch,
  tabsRefreshToken,
  calibrationExpiry = null,
  actionsNode,
  onTabChange,
  onExpiryChange,
  onRetry,
  onBackToList,
  now = Date.now(),
}: DeviceDetailBodyProps) {
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;
  const freshness = data ? evaluateDeviceFreshness(data, now) : null;
  const heartbeatStale = freshness?.level === "stale";
  const calibrationStale = (calibrationExpiry?.expiredCount ?? 0) > 0;

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
          <Button type="primary" onClick={onBackToList}>
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
  } else if (data && freshness) {
    const staleDescription = [
      heartbeatStale
        ? `最近心跳 ${formatDateTime(
            data.last_heartbeat_at ?? null,
          )}，已超过服务端下发的新鲜度阈值；状态可能已过期，请刷新或重新检测。`
        : null,
      calibrationStale
        ? `已加载范围中有 ${calibrationExpiry?.expiredCount ?? 0} 条标定已过期${
            calibrationExpiry?.latestExpiredValidTo
              ? `（最近有效期截止：${formatDateTime(
                  calibrationExpiry.latestExpiredValidTo,
                )}）`
              : ""
          }；请重新标定后再使用。`
        : null,
      stale ? "最近一次刷新失败，当前展示的是上次成功获取的数据。" : null,
    ]
      .filter((text): text is string => Boolean(text))
      .join(" ");

    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale || heartbeatStale || calibrationStale ? (
          <PageState
            variant="stale"
            compact
            title={
              heartbeatStale
                ? "设备状态可能已过期"
                : calibrationStale
                  ? "标定可能已过期"
                  : undefined
            }
            description={staleDescription}
            onRetry={onRetry}
          />
        ) : null}
        <Card>
          {actionsNode ? <div style={{ marginBottom: 16 }}>{actionsNode}</div> : null}
          <Tabs
            activeKey={activeTab}
            onChange={onTabChange}
            items={[
              {
                key: "overview",
                label: "概览",
                children: (
                  <DeviceOverview device={data} freshness={freshness} />
                ),
              },
              {
                key: "sensors",
                label: "传感器",
                children: (
                  <SensorList
                    projectId={projectId}
                    deviceId={deviceId}
                    refreshToken={tabsRefreshToken}
                  />
                ),
              },
              {
                key: "calibrations",
                label: "标定记录",
                // 概览需要标定过期提示，提前挂载并加载标定摘要。
                forceRender: true,
                children: (
                  <CalibrationList
                    projectId={projectId}
                    deviceId={deviceId}
                    calibrationProfileId={data.calibration_profile_id}
                    refreshToken={tabsRefreshToken}
                    onExpiryChange={onExpiryChange}
                  />
                ),
              },
            ]}
          />
        </Card>
        {stale ? (
          <Link to={`/devices${listSearch}`} className="ddp-inline-link">
            返回设备列表
          </Link>
        ) : null}
      </Space>
    );
  } else {
    body = <PageState variant="empty" title="暂无设备详情" />;
  }

  return <>{body}</>;
}

function DeviceDetailContent({
  projectId,
  deviceId,
}: {
  projectId: string;
  deviceId: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = resolveDeviceDetailTab(searchParams.get("tab"));
  const listSearch = buildDeviceSearch(
    projectId,
    readDeviceListFilters(searchParams),
  );
  const navigate = useNavigate();

  // 每次主动刷新递增令牌，已挂载的传感器/标定 Tab 据此同步重新加载。
  const [tabsRefreshToken, setTabsRefreshToken] = useState(0);
  const [calibrationExpiry, setCalibrationExpiry] =
    useState<CalibrationExpirySummary | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const handleCalibrationExpiry = useCallback(
    (summary: CalibrationExpirySummary) => {
      setCalibrationExpiry((previous) =>
        previous &&
        previous.expiredCount === summary.expiredCount &&
        previous.latestExpiredValidTo === summary.latestExpiredValidTo
          ? previous
          : summary,
      );
    },
    [],
  );

  const loadDevice = useCallback(
    (signal: AbortSignal) => devicesApi.getDevice(projectId, deviceId, { signal }),
    [projectId, deviceId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadDevice);

  const refreshAll = useCallback(() => {
    reload();
    setTabsRefreshToken((token) => token + 1);
  }, [reload]);

  const handleAction = useCallback(
    async (action: DeviceAction, payload: DeviceActionPayload) => {
      if (!data) {
        return;
      }
      setBusyAction(action);
      try {
        await devicesApi.actOnDevice(projectId, data.device_id, action, payload, {
          version: data.version,
        });
        reload();
      } finally {
        setBusyAction(null);
      }
    },
    [data, projectId, reload],
  );

  const handleTabChange = useCallback(
    (key: string) => {
      const tab = resolveDeviceDetailTab(key);
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (tab === DEFAULT_DEVICE_DETAIL_TAB) {
            next.delete("tab");
          } else {
            next.set("tab", tab);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const phase = resolveDevicePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const freshness = data ? evaluateDeviceFreshness(data) : null;
  const heartbeatStale = freshness?.level === "stale";

  const title = data ? (
    <Space wrap>
      <span>{data.name}</span>
      <StatusTag
        kind="device"
        value={data.status}
        stale={heartbeatStale}
        staleHint={
          freshness?.lastHeartbeatAt
            ? `最近心跳为 ${formatDateTime(freshness.lastHeartbeatAt)}`
            : undefined
        }
      />
    </Space>
  ) : (
    "设备详情"
  );

  const description = data ? (
    <Space wrap size={4}>
      <span>{data.code}</span>
      <span>· 设备 ID：</span>
      <IdentifierValue value={data.device_id} label="设备 ID" />
      <span>· 最近心跳：{formatDateTime(data.last_heartbeat_at ?? null)}</span>
    </Space>
  ) : (
    "查看设备档案、传感器与标定记录。敏感字段与权限以服务端返回为准。"
  );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button
        onClick={() => setEditOpen(true)}
        disabled={!data || Boolean(busyAction)}
      >
        编辑设备
      </Button>
      <Link to={`/devices${listSearch}`}>
        <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
      </Link>
      <Button
        icon={<ReloadOutlined />}
        loading={refreshing}
        onClick={refreshAll}
      >
        刷新
      </Button>
    </Space>
  );

  const actionsNode = data ? (
    <DeviceActionBar
      allowedActions={data.allowed_actions}
      entityLabel="设备"
      busy={Boolean(busyAction)}
      onAction={handleAction}
    />
  ) : null;

  return (
    <>
      <PageContainer title={title} description={description} extra={extra}>
        <DeviceDetailBody
          projectId={projectId}
          deviceId={deviceId}
          data={data}
          phase={phase}
          error={error}
          stale={stale}
          activeTab={activeTab}
          listSearch={listSearch}
          tabsRefreshToken={tabsRefreshToken}
          calibrationExpiry={calibrationExpiry}
          actionsNode={actionsNode}
          onTabChange={handleTabChange}
          onExpiryChange={handleCalibrationExpiry}
          onRetry={reload}
          onBackToList={() => navigate(`/devices${listSearch}`)}
        />
      </PageContainer>
      <DeviceFormModal
        projectId={projectId}
        mode="edit"
        device={data}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onSaved={() => refreshAll()}
      />
    </>
  );
}

export default function DeviceDetailPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const { deviceId } = useParams<{ deviceId: string }>();
  const [searchParams] = useSearchParams();
  const listSearch = buildDeviceSearch(
    projectId,
    readDeviceListFilters(searchParams),
  );

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
              onClick={() => navigate(`/devices${listSearch}`)}
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
