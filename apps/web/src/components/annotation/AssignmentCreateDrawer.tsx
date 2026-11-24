import { Alert, Button, DatePicker, Drawer, Form, Input, InputNumber, Select, Space, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAssignmentCapacityPreview } from "../../hooks/useAssignmentCapacityPreview";
import { useRequest } from "../../hooks/useRequest";
import { fetchAllCursorPages } from "../../services/cursorPage";
import { projectManagementApi } from "../../services/projectManagement";
import type { AnnotationAssignmentCreateInput } from "../../services/annotationTaskApi";
import type { AnnotationBatch } from "../../types/annotation";
import { ASSIGNMENT_ROLE_VALUES } from "../../types/annotation";
import type { ProjectMember } from "../../types/project";
import { AssignmentCapacitySummary } from "./AssignmentCapacityPreviewPanel";

interface AssignmentFormValues {
  batch_id: string;
  assignee_id: string;
  assignment_role: string;
  planned_item_count: number;
  planned_minutes: number;
  lease_expires_at?: Dayjs | null;
}

interface AssignmentCreateDrawerProps {
  open: boolean;
  projectId: string;
  batches: AnnotationBatch[];
  onClose: () => void;
  onSubmit: (input: AnnotationAssignmentCreateInput) => Promise<void>;
}

const roleOptions = ASSIGNMENT_ROLE_VALUES.map((value) => ({ value, label: value }));

/**
 * 新建人员分配；`batch_id` 必须来自当前任务的批次列表。
 *
 * FR-020：选中被分配人后**立即**展示其当前负载（`useAssignmentCapacityPreview`
 * 选中即查、带防抖与取消），超容量给出红/黄警示。警示**不阻断提交**：是否
 * 放行由服务端既有 `CapacityPolicy` 硬阻断裁决，前端不复制一套阻断规则，也
 * 不承诺会自动均衡负载。
 *
 * 人员来源是项目成员下拉（真实成员，不手抄 UUID）；成员列表读取失败时降级
 * 回手填 UUID 并显式说明「此时没有负载提示」——降级可以，假装有数据不行。
 */
export default function AssignmentCreateDrawer({
  open,
  projectId,
  batches,
  onClose,
  onSubmit,
}: AssignmentCreateDrawerProps) {
  const [form] = Form.useForm<AssignmentFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadMembers = useCallback(
    async (signal: AbortSignal): Promise<ProjectMember[]> =>
      fetchAllCursorPages<ProjectMember>(
        ({ cursor, limit }) =>
          projectManagementApi.listMembers(projectId, { cursor, limit, signal }),
        (member) => member.user_id,
        { maxPages: 5 },
      ),
    [projectId],
  );
  const members = useRequest(loadMembers);
  const membersDegraded = members.error !== null;

  // 选中即查：表单值变化驱动预览请求，不需要点"预览"按钮。
  const assigneeId = Form.useWatch("assignee_id", form) as string | undefined;
  const plannedItemCount =
    (Form.useWatch("planned_item_count", form) as number | undefined) ?? 0;
  const plannedMinutes =
    (Form.useWatch("planned_minutes", form) as number | undefined) ?? 0;

  const preview = useAssignmentCapacityPreview({
    projectId,
    assigneeId: assigneeId?.trim() ? assigneeId : null,
    plannedItemCount,
    plannedMinutes,
    enabled: open,
  });

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    form.resetFields();
    form.setFieldsValue({
      batch_id: batches[0]?.batch_id,
      assignee_id: "",
      assignment_role: "ANNOTATOR",
      planned_item_count: 0,
      planned_minutes: 0,
      lease_expires_at: null,
    });
  }, [batches, form, open]);

  const assigneeLabel = useMemo(() => {
    if (!assigneeId) {
      return "";
    }
    const member = members.data?.find((item) => item.user_id === assigneeId);
    return member ? member.display_name : assigneeId;
  }, [assigneeId, members.data]);

  const memberOptions = (members.data ?? []).map((member) => ({
    value: member.user_id,
    label: `${member.display_name}（${member.role}）`,
  }));

  const handleSubmit = async () => {
    if (batches.length === 0) {
      setError("任务尚未创建批次，请先新建批次再指派人员。");
      return;
    }
    let values: AssignmentFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        batch_id: values.batch_id,
        assignee_id: values.assignee_id.trim(),
        assignment_role: values.assignment_role,
        planned_item_count: values.planned_item_count,
        planned_minutes: values.planned_minutes,
        lease_expires_at: values.lease_expires_at
          ? values.lease_expires_at.toISOString()
          : null,
      });
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "新建分配失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="新建分配"
      width={560}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={submitting}
            disabled={batches.length === 0}
            onClick={() => void handleSubmit()}
          >
            创建
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        {batches.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="任务尚未创建批次，请先新建批次再指派人员。"
          />
        ) : null}
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          选中人员后会立即显示其当前负载；超容量只是提示，最终是否放行由服务端
          容量策略决定（超限时返回原因）。
        </Typography.Paragraph>
        {membersDegraded ? (
          <Alert
            type="warning"
            showIcon
            message="项目成员列表读取失败，只能手填人员 UUID"
            description="手填模式下无法展示该人员的实时负载，请自行确认其当前工作量。"
          />
        ) : null}
        <Form form={form} layout="vertical">
          <Form.Item
            name="batch_id"
            label="批次"
            rules={[{ required: true, message: "请选择批次" }]}
          >
            <Select
              options={batches.map((batch) => ({
                value: batch.batch_id,
                label: `#${batch.sequence_no} ${batch.code} · ${batch.status}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="assignee_id"
            label="人员 ID"
            rules={[{ required: true, message: "请填写人员 UUID" }]}
          >
            {membersDegraded || members.data === null ? (
              <Input placeholder="项目成员 UUID" />
            ) : (
              <Select
                showSearch
                optionFilterProp="label"
                loading={members.loading}
                placeholder="选择项目成员"
                aria-label="人员 ID"
                options={memberOptions}
              />
            )}
          </Form.Item>
          {preview.loading ? (
            <Typography.Text type="secondary">正在查询该人员负载…</Typography.Text>
          ) : null}
          {preview.data ? (
            <AssignmentCapacitySummary
              preview={preview.data}
              assigneeLabel={assigneeLabel}
            />
          ) : null}
          {!preview.loading && preview.error && assigneeId ? (
            <Alert
              type="info"
              showIcon
              message="负载提示暂不可用"
              description="预览请求失败，不影响创建；提交后仍由服务端容量策略校验并返回结果。"
            />
          ) : null}
          <Form.Item name="assignment_role" label="角色">
            <Select options={roleOptions} />
          </Form.Item>
          <Form.Item name="planned_item_count" label="计划项数">
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="planned_minutes" label="计划分钟">
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="lease_expires_at" label="租约到期（可选）">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Space>
    </Drawer>
  );
}
