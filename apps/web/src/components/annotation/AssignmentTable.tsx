import { Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import {
  buildAnnotationWorkbenchPath,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import type { AnnotationAssignment } from "../../types/annotation";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

function columns(projectId: string): TableColumnsType<AnnotationAssignment> {
  return [
    {
      title: "分配 ID",
      dataIndex: "assignment_id",
      width: 210,
      render: (value: string) => <IdentifierValue value={value} label="分配 ID" />,
    },
    {
      title: "人员",
      dataIndex: "assignee_id",
      width: 210,
      render: (value: string) => <IdentifierValue value={value} label="人员 ID" />,
    },
    {
      title: "角色",
      dataIndex: "assignment_role",
      width: 100,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "计划 / 已领取 / 已提交 / 已通过",
      key: "counts",
      width: 230,
      render: (_value, item) => (
        <Typography.Text type="secondary">
          {item.planned_item_count} / {item.claimed_item_count} /{" "}
          {item.submitted_item_count} / {item.approved_item_count}
        </Typography.Text>
      ),
    },
    {
      title: "计划分钟",
      dataIndex: "planned_minutes",
      width: 100,
    },
    {
      title: "租约到期",
      dataIndex: "lease_expires_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "工作台",
      key: "workbench",
      width: 150,
      fixed: "right",
      render: (_value, item) =>
        item.assignment_role === "ANNOTATOR" ? (
          <Link to={buildAnnotationWorkbenchPath(projectId, item.assignment_id)}>
            进入工作台
          </Link>
        ) : (
          <Tooltip title="只有 ANNOTATOR 分配可进入标注工作台">
            <Typography.Text type="secondary">仅标注分配</Typography.Text>
          </Tooltip>
        ),
    },
  ];
}

interface AssignmentTableProps {
  projectId: string;
  items: AnnotationAssignment[];
  loading: boolean;
  error: ApiError | null;
  stale: boolean;
  onRetry: () => void;
}

export default function AssignmentTable({
  projectId,
  items,
  loading,
  error,
  stale,
  onRetry,
}: AssignmentTableProps) {
  if (loading) {
    return <PageState compact variant="loading" title="正在加载分配" />;
  }
  if (error && items.length === 0) {
    return <ApiErrorState error={error} onRetry={onRetry} compact />;
  }
  if (items.length === 0) {
    return (
      <PageState
        compact
        variant="empty"
        title="暂无分配"
        description="任务尚未创建人员分配。"
      />
    );
  }
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="分配刷新失败，展示上次成功获取的数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Table<AnnotationAssignment>
        rowKey="assignment_id"
        size="small"
        columns={columns(projectId)}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1300 }}
      />
    </Space>
  );
}
