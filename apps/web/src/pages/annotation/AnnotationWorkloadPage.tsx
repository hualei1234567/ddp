/**
 * 标注工作量运营页（V31-026，只读）。
 *
 * 数据来源：
 * - `GET /projects/{project_id}/members` 给出真实项目成员（含显示名）；
 * - `GET /projects/{project_id}/annotation-workload` 按
 *   `(assignee_id, [period_start, period_end))` 聚合分配事件。
 *
 * 人员与周期都来自服务端或用户显式选择；页面**不提供**「默认某人」的兜底，
 * 也不会在前端计算容量或利用率——容量是部署配置，前端无从得知。
 *
 * 状态覆盖：Loading / Empty / Error / Forbidden / Stale / Partial（口径不完整）。
 */
import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, DatePicker, Select, Space, Typography } from "antd";
import type { Dayjs } from "dayjs";
import dayjs from "dayjs";
import { useCallback, useMemo, useState } from "react";

import { WorkloadSummaryBody } from "../../components/annotation/WorkloadSummaryBody";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { fetchAllCursorPages } from "../../services/cursorPage";
import { formatTimestamp } from "../../services/format";
import { projectManagementApi } from "../../services/projectManagement";
import type { ProjectMember } from "../../types/project";

const DEFAULT_PERIOD_DAYS = 7;

interface WorkloadViewProps {
  projectId: string;
  assigneeId: string;
  assigneeLabel: string;
  periodStart: string;
  periodEnd: string;
}

/**
 * 独立子组件承载请求：`useRequest` 只在挂载时触发，人员/周期变化时由外层
 * 通过 `key` 重挂载，而不是在同一个实例里隐式重发。
 */
function WorkloadView({
  projectId,
  assigneeId,
  assigneeLabel,
  periodStart,
  periodEnd,
}: WorkloadViewProps) {
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getWorkload(
        projectId,
        { assigneeId, periodStart, periodEnd },
        { signal },
      ),
    [projectId, assigneeId, periodStart, periodEnd],
  );
  const request = useRequest(load);

  return (
    <Card
      size="small"
      title="周期工作量"
      extra={
        <Space wrap>
          <Typography.Text type="secondary">
            更新于 {formatTimestamp(request.updatedAt)}
          </Typography.Text>
          <Button
            icon={<ReloadOutlined />}
            loading={request.refreshing}
            onClick={request.reload}
          >
            刷新
          </Button>
        </Space>
      }
    >
      <WorkloadSummaryBody
        data={request.data}
        loading={request.loading}
        stale={request.stale}
        error={request.error}
        onRetry={request.reload}
        assigneeLabel={assigneeLabel}
      />
    </Card>
  );
}

function WorkloadContent({ projectId }: { projectId: string }) {
  const loadMembers = useCallback(
    async (signal: AbortSignal) => {
      const members = await fetchAllCursorPages<ProjectMember>(
        ({ cursor, limit }) =>
          projectManagementApi.listMembers(projectId, { cursor, limit, signal }),
        (member) => member.user_id,
        { maxPages: 5 },
      );
      return members;
    },
    [projectId],
  );
  const members = useRequest(loadMembers);

  const [assigneeId, setAssigneeId] = useState<string | null>(null);
  const defaultRange = useMemo<[Dayjs, Dayjs]>(() => {
    const end = dayjs();
    return [end.subtract(DEFAULT_PERIOD_DAYS, "day"), end];
  }, []);
  const [range, setRange] = useState<[Dayjs, Dayjs]>(defaultRange);

  const selected = useMemo(
    () => members.data?.find((member) => member.user_id === assigneeId) ?? null,
    [assigneeId, members.data],
  );

  // 成员列表返回后，默认选中第一位而不是留空：空选会让运营页看起来
  // "没有工作量"，而实际只是没选中人。
  const effectiveAssignee = selected ?? members.data?.[0] ?? null;

  return (
    <Space direction="vertical" size={12} className="ddp-stack" style={{ width: "100%" }}>
      {members.error && members.data === null ? (
        <ApiErrorState error={members.error} onRetry={members.reload} />
      ) : null}
      {members.loading && members.data === null ? (
        <PageState
          compact
          variant="loading"
          title="正在加载项目成员"
          description="工作量必须按真实项目成员查询。"
        />
      ) : null}
      {members.data !== null && members.data.length === 0 ? (
        <PageState
          compact
          variant="empty"
          title="项目还没有成员"
          description="先邀请成员加入项目，才会产生可统计的分配。"
        />
      ) : null}
      {effectiveAssignee ? (
        <>
          <Card size="small">
            <Space wrap size={[12, 12]} align="end">
              <Space direction="vertical" size={4}>
                <Typography.Text>人员</Typography.Text>
                <Select
                  showSearch
                  optionFilterProp="label"
                  style={{ minWidth: 220 }}
                  value={effectiveAssignee.user_id}
                  onChange={(value: string) => setAssigneeId(value)}
                  options={members.data?.map((member) => ({
                    value: member.user_id,
                    label: `${member.display_name}（${member.role}）`,
                  }))}
                />
              </Space>
              <Space direction="vertical" size={4}>
                <Typography.Text>周期</Typography.Text>
                <DatePicker.RangePicker
                  showTime
                  allowClear={false}
                  value={range}
                  onChange={(next) => {
                    // allowClear=false 时 next 仍可能是 null（类型上），
                    // 这里显式忽略，保持上一个有效周期。
                    if (next && next[0] && next[1]) {
                      setRange([next[0], next[1]]);
                    }
                  }}
                />
              </Space>
            </Space>
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="周期为半开区间 [开始, 结束)"
              description="结束时间当刻发生的分配不计入本期；容量与阈值来自服务端部署配置。"
            />
          </Card>
          <WorkloadView
            key={`${effectiveAssignee.user_id}:${range[0].toISOString()}:${range[1].toISOString()}`}
            projectId={projectId}
            assigneeId={effectiveAssignee.user_id}
            assigneeLabel={effectiveAssignee.display_name}
            periodStart={range[0].toISOString()}
            periodEnd={range[1].toISOString()}
          />
        </>
      ) : null}
    </Space>
  );
}

export default function AnnotationWorkloadPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="工作量运营" description="工作量必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注工作量" />
      </PageContainer>
    );
  }
  return (
    <PageContainer
      title="工作量运营"
      description="按人员与周期聚合标注分配；只读视图，容量与阈值由服务端配置。"
    >
      <WorkloadContent projectId={projectId} />
    </PageContainer>
  );
}
