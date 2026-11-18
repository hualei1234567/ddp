import { ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  DatePicker,
  Descriptions,
  Grid,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import type { ApiError } from "../../services/api";
import {
  applyEpisodeListFilterPatch,
  buildEpisodeDetailPath,
  buildTaskDetailPath,
  episodeServerFilterKey,
  hasEpisodeListFilters,
  readEpisodeListFilters,
  type EpisodeListFilters,
} from "../../services/collectionUrl";
import {
  formatNanoseconds,
  getAnnotationStatus,
  getEpisodeStatus,
  getQualityStatus,
} from "../../services/collectionPresentation";
import { collectionsApi } from "../../services/collections";
import { formatDateTime, formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { PagePhase } from "../../services/pagePhase";
import {
  EPISODE_ANNOTATION_STATUS_VALUES,
  EPISODE_QUALITY_STATUS_VALUES,
  EPISODE_STATUS_VALUES,
  type Episode,
} from "../../types/collection";

const STATUS_OPTIONS = EPISODE_STATUS_VALUES.map((value) => ({
  value,
  label: getEpisodeStatus(value).label,
}));

const QUALITY_OPTIONS = EPISODE_QUALITY_STATUS_VALUES.map((value) => ({
  value,
  label: getQualityStatus(value).label,
}));

const ANNOTATION_OPTIONS = EPISODE_ANNOTATION_STATUS_VALUES.map((value) => ({
  value,
  label: getAnnotationStatus(value).label,
}));

function filterEpisodes(
  items: Episode[],
  filters: EpisodeListFilters,
): Episode[] {
  const keyword = filters.keyword.toLowerCase();
  if (keyword === "") {
    return items;
  }
  return items.filter((item) =>
    [item.code, item.episode_id, item.manifest_key ?? ""].some((value) =>
      value.toLowerCase().includes(keyword),
    ),
  );
}

function buildColumns(projectId: string): TableColumnsType<Episode> {
  return [
    {
      title: "Episode",
      key: "episode",
      width: 220,
      fixed: "left",
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.code}</Typography.Text>
          <Typography.Text type="secondary">
            版本 v{record.episode_version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "Episode ID",
      dataIndex: "episode_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue value={value} label="Episode ID" />
      ),
    },
    {
      title: "任务 / 批次",
      key: "source",
      width: 190,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          {record.task_id ? (
            <Link to={buildTaskDetailPath(projectId, record.task_id)}>
              <Typography.Text code>任务</Typography.Text>
            </Link>
          ) : (
            <Typography.Text type="secondary">—</Typography.Text>
          )}
          <IdentifierValue value={record.batch_id} label="批次 ID" />
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => (
        <CollectionStatusTag kind="episode" value={value} />
      ),
    },
    {
      title: "质量",
      dataIndex: "quality_status",
      width: 110,
      render: (value: string) => (
        <CollectionStatusTag kind="quality" value={value} />
      ),
    },
    {
      title: "时间范围",
      key: "time",
      width: 250,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>{formatNanoseconds(record.start_time_ns)}</span>
          <Typography.Text type="secondary">
            至 {formatNanoseconds(record.end_time_ns)}
          </Typography.Text>
          <Typography.Text type="secondary">
            时长 {formatNanoseconds(record.duration_ns)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "帧数",
      dataIndex: "frame_count",
      width: 100,
      render: (value: number) => value.toLocaleString("zh-CN"),
    },
    {
      title: "切片规则 / Pipeline",
      key: "versions",
      width: 220,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>规则 {record.segmentation_rule_version}</span>
          <Typography.Text type="secondary">
            {record.pipeline_id}@{record.pipeline_version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      sorter: (left, right) =>
        (left.updated_at ?? "").localeCompare(right.updated_at ?? ""),
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      fixed: "right",
      render: (_value, record) => (
        <Link to={buildEpisodeDetailPath(projectId, record.episode_id)}>
          查看详情
        </Link>
      ),
    },
  ];
}

export interface EpisodeListBodyProps {
  projectId: string;
  filters: EpisodeListFilters;
  phase: PagePhase;
  error: ApiError | null;
  items: Episode[];
  filteredItems: Episode[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  stale: boolean;
  updatedAt: number | null;
  isMobile: boolean;
  onRetry: () => void;
  onResetFilters: () => void;
  onLoadMore: () => void;
}

export function EpisodeListBody({
  projectId,
  filters,
  phase,
  error,
  items,
  filteredItems,
  hasMore,
  loading,
  refreshing,
  loadingMore,
  loadMoreError,
  stale,
  isMobile,
  onRetry,
  onResetFilters,
  onLoadMore,
}: EpisodeListBodyProps) {
  const filtersActive = hasEpisodeListFilters(filters);
  const columns = useMemo(() => buildColumns(projectId), [projectId]);
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;
  const notChecked = items.filter(
    (item) => item.quality_status === "NOT_CHECKED",
  ).length;

  let body: ReactNode;
  if (phase === "forbidden") {
    body = errorBody;
  } else if (phase === "not-found") {
    body = (
      <PageState
        variant="empty"
        title="项目不存在或无权查看"
        description="服务端未找到该 Episode 范围；跨项目资源统一不可见。"
        action={<Button onClick={onRetry}>重试</Button>}
      />
    );
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载 Episode"
        description="正在按当前项目加载切片结果、质量状态与版本信息。"
      />
    );
  } else if (phase === "error") {
    body = errorBody;
  } else {
    let content: ReactNode;
    if (items.length === 0) {
      content = (
        <PageState
          variant="empty"
          title={filtersActive ? "无匹配 Episode" : "当前项目还没有 Episode"}
          description={
            filtersActive
              ? hasMore
                ? "已加载 Episode 中没有匹配项；还有更多未加载，可继续加载后再筛选。"
                : "当前筛选条件下没有 Episode，可清空筛选后重试。"
              : "Episode 由接入切片 Job 生成；阶段 2 前端不伪造切片结果。"
          }
          action={
            filtersActive ? (
              <Button onClick={onResetFilters}>清空筛选</Button>
            ) : undefined
          }
        />
      );
    } else if (filteredItems.length === 0) {
      content = (
        <PageState
          variant="empty"
          title="无匹配 Episode"
          description={
            hasMore
              ? "已加载的 Episode 中没有匹配项；还有更多未加载，可继续加载后再筛选。"
              : "当前筛选条件下没有 Episode，可清空筛选后重试。"
          }
          action={<Button onClick={onResetFilters}>清空筛选</Button>}
        />
      );
    } else if (isMobile) {
      content = (
        <Space direction="vertical" size={12} className="ddp-stack">
          {filteredItems.map((episode) => (
            <Card
              key={episode.episode_id}
              title={
                <Space wrap>
                  <span>{episode.code}</span>
                  <CollectionStatusTag kind="episode" value={episode.status} />
                  <CollectionStatusTag
                    kind="quality"
                    value={episode.quality_status}
                  />
                </Space>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Episode ID">
                  <IdentifierValue
                    value={episode.episode_id}
                    label="Episode ID"
                  />
                </Descriptions.Item>
                <Descriptions.Item label="任务">
                  {episode.task_id ? (
                    <Link to={buildTaskDetailPath(projectId, episode.task_id)}>
                      查看任务
                    </Link>
                  ) : (
                    "—"
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="时间范围">
                  {formatNanoseconds(episode.start_time_ns)} 至{" "}
                  {formatNanoseconds(episode.end_time_ns)}
                </Descriptions.Item>
                <Descriptions.Item label="时长">
                  {formatNanoseconds(episode.duration_ns)}
                </Descriptions.Item>
                <Descriptions.Item label="帧数">
                  {episode.frame_count.toLocaleString("zh-CN")}
                </Descriptions.Item>
                <Descriptions.Item label="切片规则">
                  {episode.segmentation_rule_version}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {formatDateTime(episode.updated_at)}
                </Descriptions.Item>
              </Descriptions>
              <Link to={buildEpisodeDetailPath(projectId, episode.episode_id)}>
                查看详情
              </Link>
            </Card>
          ))}
        </Space>
      );
    } else {
      content = (
        <Table<Episode>
          rowKey="episode_id"
          columns={columns}
          dataSource={filteredItems}
          pagination={false}
          scroll={{ x: 1700 }}
        />
      );
    }

    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功获取的 Episode 数据。"
            onRetry={onRetry}
          />
        ) : null}
        {notChecked > 0 ? (
          <PageState
            variant="partial"
            compact
            title="部分 Episode 尚未质检"
            description={`已加载范围中有 ${notChecked} 条 Episode 的质量状态为 NOT_CHECKED；READY 不等于 ACCEPTED，未质检数据不得视为可用交付。`}
          />
        ) : null}
        {error && !stale ? (
          <ApiErrorState error={error} onRetry={onRetry} compact />
        ) : null}
        <Space wrap>
          <Tag color="blue">已加载 {items.length} 条 Episode</Tag>
          {notChecked > 0 ? <Tag color="orange">未质检 {notChecked}</Tag> : null}
        </Space>
        <Typography.Text type="secondary">
          {hasMore
            ? `关键词只作用于已加载的 ${items.length} 条 Episode；状态与任务筛选由服务端执行。`
            : `统计覆盖当前项目全部 ${items.length} 条 Episode；关键词作用于该范围。`}
        </Typography.Text>
        {content}
        <LoadMoreBar
          loadedCount={items.length}
          unit="条 Episode"
          hasMore={hasMore}
          loadingMore={loadingMore}
          disabled={loading || refreshing}
          error={loadMoreError}
          onLoadMore={onLoadMore}
          block={isMobile}
        />
      </Space>
    );
  }

  return <>{body}</>;
}

function capturedRange(filters: EpisodeListFilters): [Dayjs, Dayjs] | null {
  if (!filters.capturedFrom || !filters.capturedTo) {
    return null;
  }
  const start = dayjs(filters.capturedFrom);
  const end = dayjs(filters.capturedTo);
  return start.isValid() && end.isValid() ? [start, end] : null;
}

export function EpisodeFilters({
  filters,
  loadedCount,
  hasMore,
  disabled,
  onChange,
  onReset,
  stacked,
}: {
  filters: EpisodeListFilters;
  loadedCount: number;
  hasMore: boolean;
  disabled: boolean;
  onChange: (patch: Partial<EpisodeListFilters>) => void;
  onReset: () => void;
  stacked: boolean;
}) {
  const [keyword, setKeyword] = useState(filters.keyword);
  const [taskId, setTaskId] = useState(filters.taskId);
  const [robotId, setRobotId] = useState(filters.robotId);
  const [camera, setCamera] = useState(filters.camera);

  useEffect(() => setKeyword(filters.keyword), [filters.keyword]);
  useEffect(() => setTaskId(filters.taskId), [filters.taskId]);
  useEffect(() => setRobotId(filters.robotId), [filters.robotId]);
  useEffect(() => setCamera(filters.camera), [filters.camera]);

  const fullWidth = stacked ? { width: "100%" } : undefined;

  return (
    <div className={stacked ? "ddp-filters ddp-filters-stacked" : "ddp-filters"}>
      <Input.Search
        allowClear
        value={keyword}
        placeholder="搜索已加载 Episode（编号、ID、Manifest Key）"
        style={fullWidth ?? { width: 300, maxWidth: "100%" }}
        onChange={(event) => setKeyword(event.target.value)}
        onSearch={(value) => onChange({ keyword: value.trim() })}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="Episode 状态（服务端筛选）"
        style={fullWidth ?? { width: 190 }}
        value={filters.status || undefined}
        options={STATUS_OPTIONS}
        disabled={disabled}
        onChange={(value) => onChange({ status: value ?? "" })}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="质量状态（服务端筛选）"
        style={fullWidth ?? { width: 170 }}
        value={filters.qualityStatus || undefined}
        options={QUALITY_OPTIONS}
        disabled={disabled}
        onChange={(value) => onChange({ qualityStatus: value ?? "" })}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="标注状态（服务端筛选）"
        style={fullWidth ?? { width: 170 }}
        value={filters.annotationStatus || undefined}
        options={ANNOTATION_OPTIONS}
        disabled={disabled}
        onChange={(value) => onChange({ annotationStatus: value ?? "" })}
      />
      <Input.Search
        allowClear
        value={taskId}
        placeholder="采集任务 ID（服务端筛选）"
        style={fullWidth ?? { width: 300, maxWidth: "100%" }}
        onChange={(event) => setTaskId(event.target.value)}
        onSearch={(value) => onChange({ taskId: value.trim() })}
      />
      <Input.Search
        allowClear
        value={robotId}
        placeholder="机器人 ID（服务端筛选）"
        style={fullWidth ?? { width: 300, maxWidth: "100%" }}
        onChange={(event) => setRobotId(event.target.value)}
        onSearch={(value) => onChange({ robotId: value.trim() })}
      />
      <Input.Search
        allowClear
        value={camera}
        placeholder="机位标识，如 main（服务端筛选）"
        style={fullWidth ?? { width: 260, maxWidth: "100%" }}
        onChange={(event) => setCamera(event.target.value)}
        onSearch={(value) => onChange({ camera: value.trim() })}
      />
      <DatePicker.RangePicker
        allowClear
        showTime
        value={capturedRange(filters)}
        style={fullWidth}
        disabled={disabled}
        placeholder={["捕获开始时间", "捕获结束时间"]}
        onChange={(value) => {
          if (!value || !value[0] || !value[1]) {
            onChange({ capturedFrom: "", capturedTo: "" });
            return;
          }
          // 区间按用户选择的时刻闭区间提交；ISO 串即 UTC，服务端要求带时区。
          onChange({
            capturedFrom: value[0].toISOString(),
            capturedTo: value[1].toISOString(),
          });
        }}
      />
      <Button block={stacked} onClick={onReset}>
        重置
      </Button>
      <Typography.Text type="secondary" className="ddp-filter-scope">
        关键词作用于已加载的 {loadedCount} 条 Episode；其余维度由服务端筛选
        {hasMore ? "（可继续加载更多）" : ""}
      </Typography.Text>
    </div>
  );
}

function EpisodeListContent({
  projectId,
  filters,
}: {
  projectId: string;
  filters: EpisodeListFilters;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams] = useSearchParams();

  const loadEpisodes = useCallback(
    (options: CursorPageRequestOptions) =>
      collectionsApi.listEpisodes(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status: filters.status || undefined,
        collectionTaskId: filters.taskId || undefined,
        robotId: filters.robotId || undefined,
        qualityStatus: filters.qualityStatus || undefined,
        annotationStatus: filters.annotationStatus || undefined,
        camera: filters.camera || undefined,
        capturedFrom: filters.capturedFrom || undefined,
        capturedTo: filters.capturedTo || undefined,
      }),
    [
      projectId,
      filters.status,
      filters.taskId,
      filters.robotId,
      filters.qualityStatus,
      filters.annotationStatus,
      filters.camera,
      filters.capturedFrom,
      filters.capturedTo,
    ],
  );

  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    updatedAt,
    reload,
    loadMore,
  } = useCursorPage(loadEpisodes, (episode) => episode.episode_id);

  const filteredItems = useMemo(
    () => filterEpisodes(items, filters),
    [items, filters],
  );
  const phase = resolvePagePhase({
    hasData: updatedAt !== null,
    loading,
    error,
  });
  const isMobile = screens.md === false;

  const updateFilter = useCallback(
    (patch: Partial<EpisodeListFilters>) => {
      const params = applyEpisodeListFilterPatch(searchParams, patch);
      const query = params.toString();
      navigate(
        {
          pathname: "/episodes",
          search: query ? `?${query}` : "",
        },
        { replace: true },
      );
    },
    [navigate, searchParams],
  );

  const resetFilters = useCallback(() => {
    const params = new URLSearchParams();
    const projectIdParam = searchParams.get("project_id");
    if (projectIdParam) {
      params.set("project_id", projectIdParam);
    }
    const query = params.toString();
    navigate(
      { pathname: "/episodes", search: query ? `?${query}` : "" },
      { replace: true },
    );
  }, [navigate, searchParams]);

  const filtersElement =
    phase === "forbidden" ? undefined : (
      <EpisodeFilters
        filters={filters}
        loadedCount={items.length}
        hasMore={hasMore}
        disabled={items.length === 0}
        onChange={updateFilter}
        onReset={resetFilters}
        stacked={isMobile}
      />
    );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer
      title="Episode 管理"
      description="按服务端 Episode 接口展示状态、质量状态、时间语义与切片版本。READY 不等于 ACCEPTED；详情页可查看动作、修订与正反向血缘。"
      extra={extra}
      filters={filtersElement}
    >
      <EpisodeListBody
        projectId={projectId}
        filters={filters}
        phase={phase}
        error={error}
        items={items}
        filteredItems={filteredItems}
        hasMore={hasMore}
        loading={loading}
        refreshing={refreshing}
        loadingMore={loadingMore}
        loadMoreError={loadMoreError}
        stale={stale}
        updatedAt={updatedAt}
        isMobile={isMobile}
        onRetry={reload}
        onResetFilters={resetFilters}
        onLoadMore={loadMore}
      />
    </PageContainer>
  );
}

export default function EpisodeListPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const filters = readEpisodeListFilters(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="Episode 管理" description="Episode 必须按项目隔离展示。">
        <PageState
          variant="empty"
          title="未选择项目"
          description="请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。"
          action={
            <Button type="primary" onClick={() => navigate("/")}>
              返回工作台
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <EpisodeListContent
      key={`${projectId}:${episodeServerFilterKey(filters)}`}
      projectId={projectId}
      filters={filters}
    />
  );
}
