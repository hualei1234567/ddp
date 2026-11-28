import { ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useEffect, useState } from "react";

import { ApiError } from "../../services/api";
import {
  formatFrameSeconds,
  presentDecodeStatus,
} from "../../services/videoFrameParsers";
import { videoFramesApi, type VideoFramesApi } from "../../services/videoFrames";
import type {
  EpisodeVideoFramesResponse,
  VideoFrame,
} from "../../types/videoFrames";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

const DEFAULT_PAGE_SIZE = 20;

const COLUMNS: TableColumnsType<VideoFrame> = [
  {
    title: "帧序号",
    dataIndex: "frame_index",
    width: 90,
    render: (value: number) => <Typography.Text code>{value}</Typography.Text>,
  },
  {
    title: "视频流",
    dataIndex: "stream_id",
    width: 150,
    render: (value: string) => <Typography.Text>{value}</Typography.Text>,
  },
  {
    title: "关键帧",
    dataIndex: "is_keyframe",
    width: 96,
    render: (value: boolean) =>
      value ? <Tag color="blue">关键帧</Tag> : <Tag>普通帧</Tag>,
  },
  {
    title: "解码状态",
    dataIndex: "decode_status",
    width: 100,
    render: (value: string) => {
      const presentation = presentDecodeStatus(value);
      return <Tag color={presentation.color}>{presentation.label}</Tag>;
    },
  },
  {
    title: "原始 PTS",
    dataIndex: "pts_seconds",
    width: 140,
    render: (value: number | null) => (
      <Typography.Text code>{formatFrameSeconds(value)}</Typography.Text>
    ),
  },
  {
    title: "原始时间戳",
    dataIndex: "timestamp_raw",
    width: 160,
    render: (value: string | null, row) => {
      if (value === null) {
        return "—";
      }
      return (
        <Space size={4} wrap>
          <Typography.Text code>{value}</Typography.Text>
          {row.time_base ? (
            <Typography.Text type="secondary">({row.time_base})</Typography.Text>
          ) : null}
        </Space>
      );
    },
  },
  {
    title: "归一化时间",
    dataIndex: "timestamp_normalized",
    width: 200,
    render: (value: number | null, row) => (
      <Space size={6} wrap>
        <Typography.Text code>{formatFrameSeconds(value)}</Typography.Text>
        {row.normalize_method ? (
          <Tag color={value !== null ? "cyan" : "default"}>
            {row.normalize_method}
          </Tag>
        ) : null}
      </Space>
    ),
  },
  {
    title: "错误信息",
    dataIndex: "error_message",
    render: (value: string | null) =>
      value ? <Typography.Text type="danger">{value}</Typography.Text> : "—",
  },
];

function DecodeDistributionBar({
  total,
  decoded,
  failed,
}: {
  total: number;
  decoded: number;
  failed: number;
}) {
  const skipped = Math.max(0, total - decoded - failed);
  const okPercent = total > 0 ? Math.round((decoded / total) * 100) : 0;
  const failPercent = total > 0 ? Math.round((failed / total) * 100) : 0;

  return (
    <Space direction="vertical" size={6} style={{ width: "100%" }}>
      <Space wrap size={8}>
        <Typography.Text type="secondary">解码状态分布：</Typography.Text>
        <Tag color="success">
          成功 {decoded.toLocaleString("zh-CN")}（{okPercent}%）
        </Tag>
        <Tag color={failed > 0 ? "error" : "default"}>
          失败 {failed.toLocaleString("zh-CN")}（{failPercent}%）
        </Tag>
        {skipped > 0 ? (
          <Tag color="warning">跳过 {skipped.toLocaleString("zh-CN")}</Tag>
        ) : null}
      </Space>
      <Progress
        percent={okPercent}
        status={failed > 0 ? "exception" : "normal"}
        showInfo={false}
        size="small"
      />
    </Space>
  );
}

/**
 * Episode 详情页的「视频帧索引与时间轴」只读面板（V6.1 Sprint 5 · F3）。
 *
 * 所有帧数统计、解码状态与时间对比均直接读取服务端 `video_frames` 索引，
 * 无关联原始资产或无帧时显式呈现空态，绝不构造假数据。
 */
export default function EpisodeVideoFramesPanel({
  projectId,
  episodeId,
  api = videoFramesApi,
}: {
  projectId: string;
  episodeId: string;
  api?: VideoFramesApi;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [version, setVersion] = useState(0);
  const [data, setData] = useState<EpisodeVideoFramesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .listForEpisode(projectId, episodeId, {
        offset: (page - 1) * pageSize,
        limit: pageSize,
        signal: controller.signal,
      })
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        setData(response);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError({
                kind: "network",
                message: "视频帧索引加载失败，请检查网络或服务状态。",
                cause: caught,
              }),
        );
        setLoading(false);
      });
    return () => controller.abort();
  }, [api, episodeId, page, pageSize, projectId, version]);

  const reload = () => setVersion((current) => current + 1);

  return (
    <Card
      title="视频帧索引与时间轴"
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={reload}
        >
          刷新
        </Button>
      }
    >
      {loading && data === null ? (
        <PageState
          compact
          variant="loading"
          title="正在加载视频帧索引"
          description="读取 Episode 关联原始资产的视频帧解码状态与时间归一化信息。"
        />
      ) : error !== null && data === null ? (
        <ApiErrorState compact error={error} onRetry={reload} />
      ) : data === null || data.total_frames === 0 ? (
        <PageState
          compact
          variant="empty"
          title="暂无视频帧索引"
          description={
            data && data.raw_asset_ids.length === 0
              ? "当前 Episode 尚未关联原始视频资产，帧数汇总为 0。"
              : "当前 Episode 关联的原始资产尚未生成视频帧索引记录。"
          }
        />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Row gutter={[16, 16]}>
            <Col xs={12} sm={6}>
              <Statistic title="总帧数" value={data.total_frames} />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic title="解码成功" value={data.decoded_count} />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="解码失败"
                value={data.failed_count}
                valueStyle={
                  data.failed_count > 0 ? { color: "#cf1322" } : undefined
                }
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic title="已时间归一化" value={data.normalized_count} />
            </Col>
          </Row>

          <DecodeDistributionBar
            total={data.total_frames}
            decoded={data.decoded_count}
            failed={data.failed_count}
          />

          <Table<VideoFrame>
            rowKey={(row) => `${row.stream_id}:${row.frame_index}`}
            size="small"
            loading={loading}
            columns={COLUMNS}
            dataSource={data.items}
            scroll={{ x: 960 }}
            pagination={{
              current: page,
              pageSize,
              total: data.total_frames,
              showSizeChanger: true,
              pageSizeOptions: [20, 50, 100],
              showTotal: (total) => `共 ${total.toLocaleString("zh-CN")} 帧`,
              onChange: (nextPage, nextPageSize) => {
                setPage(nextPageSize !== pageSize ? 1 : nextPage);
                setPageSize(nextPageSize);
              },
            }}
          />
        </Space>
      )}
    </Card>
  );
}
