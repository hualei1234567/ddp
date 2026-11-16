import { Form, InputNumber, Modal, Space, Tag, Typography, message } from "antd";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../../services/api";
import {
  describeQuotaWriteError,
  presentLimit,
  presentUsed,
  resourceKindLabel,
  UNLIMITED_LIMIT_LABEL,
  validateQuotaLimit,
  type UsageRow,
} from "../../services/analyticsPresentation";
import { formatDateTime } from "../../services/format";
import { governanceApi } from "../../services/governance";
import type { Quota, ResourceKind } from "../../types/governance";

interface QuotaLimitValues {
  limit: number | null;
}

export interface QuotaEditModalProps {
  projectId: string;
  /** 正在编辑的资源类型；`null` 表示弹窗关闭。 */
  kind: ResourceKind | null;
  /** 当前行（含已配置配额的版本号）；随报表刷新而变化。 */
  row: UsageRow | null;
  online: boolean;
  onClose: () => void;
  /**
   * 写成功或本地版本失效（409 / 428）后刷新报表与配额版本。
   * 刷新后 `row.quotaVersion` 会更新，用户可原地重试。
   */
  onRefreshAfterWrite: () => void;
}

/**
 * 配额上限编辑。
 *
 * - 新建（该类型尚未配置上限）不需要 `If-Match`；
 * - 修改必须带 `If-Match: "<version>"`，缺失服务端返回 428、版本不符 409；
 * - 没有删除操作：契约刻意不提供，避免「未配置」与「上限为 0」混淆。
 *
 * 403 等拒绝一律如实提示，不通过隐藏入口来假装没有权限。
 */
export default function QuotaEditModal({
  projectId,
  kind,
  row,
  online,
  onClose,
  onRefreshAfterWrite,
}: QuotaEditModalProps) {
  const [form] = Form.useForm<QuotaLimitValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const draftLimit = Form.useWatch("limit", form);
  const open = kind !== null && row !== null;
  /** 只在「换了一类资源」时回填，避免 409 后刷新把用户刚输入的数值冲掉。 */
  const initializedKindRef = useRef<ResourceKind | null>(null);

  useEffect(() => {
    if (!open || !row) {
      initializedKindRef.current = null;
      return;
    }
    if (initializedKindRef.current === row.entry.resource_kind) {
      return;
    }
    initializedKindRef.current = row.entry.resource_kind;
    // 未配置上限时留空，而不是填 0——0 是「禁止任何新增」，与「未配置」不同。
    form.setFieldsValue({ limit: row.entry.limit });
  }, [open, row, form]);

  const submit = async () => {
    if (!kind || !row) {
      return;
    }
    let values: QuotaLimitValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const limit = values.limit;
    if (limit === null || limit === undefined) {
      return;
    }

    setSubmitting(true);
    try {
      const quota: Quota = await governanceApi.setQuota(projectId, kind, limit, {
        version: row.quotaVersion,
      });
      messageApi.success(
        `${resourceKindLabel(kind)}上限已保存，配额版本 v${quota.version}`,
      );
      onRefreshAfterWrite();
      onClose();
    } catch (caught) {
      if (caught instanceof ApiError) {
        const failure = describeQuotaWriteError(caught);
        messageApi.error(failure.message);
        if (failure.refreshRequired) {
          // 版本已失效：拉取最新配额，弹窗保留，用户可直接重试。
          onRefreshAfterWrite();
        }
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "保存失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  /**
   * 新上限低于当前已用量时提前说明后果：服务端会在写入前拒绝新增
   * （409 quota_exceeded），已有数据不会被删除。这只是把契约后果说清楚，
   * 是否允许保存仍由服务端裁决。
   */
  const limitBelowUsed =
    row !== null &&
    typeof draftLimit === "number" &&
    row.entry.used !== null &&
    draftLimit < row.entry.used;

  return (
    <>
      {contextHolder}
      <Modal
        title={
          kind === null
            ? "设置配额上限"
            : `设置「${resourceKindLabel(kind)}」上限`
        }
        open={open}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !online }}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        {!online ? (
          <Typography.Paragraph type="warning">
            当前处于离线状态，无法提交配额修改。
          </Typography.Paragraph>
        ) : null}
        {row !== null ? (
          <Typography.Paragraph type="secondary">
            当前已用：{presentUsed(row.entry)}；当前上限：
            {row.entry.limit === null
              ? UNLIMITED_LIMIT_LABEL
              : presentLimit(row.entry)}
            ；配额版本：
            {row.quotaVersion === null ? (
              <Tag>未配置</Tag>
            ) : (
              <Tag color="blue">v{row.quotaVersion}</Tag>
            )}
            {row.quotaVersion === null ? null : (
              <span>
                （修改会携带 If-Match: &quot;{row.quotaVersion}&quot;）
              </span>
            )}
          </Typography.Paragraph>
        ) : null}
        {row !== null && limitBelowUsed ? (
          <Typography.Paragraph type="warning">
            新上限低于当前已用量（{presentUsed(row.entry)}），
            保存后该类资源的新增写入会被服务端拒绝（409 quota_exceeded），
            已有数据不会被删除。
          </Typography.Paragraph>
        ) : null}
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="limit"
            label="上限"
            extra="0 是合法上限（禁止任何新增），与「未配置上限」不同；保存后不能删除上限，只能继续调整。"
            rules={[
              {
                validator: async (_rule: unknown, value: number | null) => {
                  const problem = validateQuotaLimit(value);
                  if (problem !== null) {
                    throw new Error(problem);
                  }
                },
              },
            ]}
          >
            <InputNumber
              min={0}
              precision={0}
              style={{ width: "100%" }}
              placeholder="请输入非负整数"
            />
          </Form.Item>
        </Form>
        {row !== null && row.entry.measured_at !== null ? (
          <Space size={4}>
            <Typography.Text type="secondary">
              报表数据统计于
            </Typography.Text>
            <Typography.Text code>
              {formatDateTime(row.entry.measured_at)}
            </Typography.Text>
          </Space>
        ) : null}
      </Modal>
    </>
  );
}
