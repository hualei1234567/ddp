import { Form, Input, InputNumber, Modal, Typography, message } from "antd";
import { useState } from "react";

import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { ApiError } from "../../services/api";
import { annotationApi } from "../../services/annotation";
import {
  METRIC_DEFINITIONS_VERSION_MAX,
  validateIouThreshold,
  validateMetricDefinitionsVersion,
} from "../../services/qualityForm";

interface IAAReportCreateValues {
  metric_definitions_version: string;
  iou_threshold: number;
}

interface IAAReportCreateModalProps {
  projectId: string;
  taskId: string;
  /** 同一任务最近一份报告的口径版本；仅作为默认值，来自服务端数据。 */
  defaultMetricDefinitionsVersion?: string | null;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

/**
 * IAA 计算表单（契约 `IAAReportCreate`）。
 *
 * - `metric_definitions_version`：1–64 字符的字符串，契约**没有**固定取值集，
 *   因此不硬编码版本号；默认值取自该任务最近一份报告，让同任务口径保持一致。
 * - `iou_threshold`：0–1，契约默认 0.5。
 * 计算是同步接口；样本不足时服务端返回 `INSUFFICIENT_DATA`，页面按 N/A 展示。
 */
export default function IAAReportCreateModal({
  projectId,
  taskId,
  defaultMetricDefinitionsVersion,
  open,
  onClose,
  onCreated,
}: IAAReportCreateModalProps) {
  const [form] = Form.useForm<IAAReportCreateValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const online = useOnlineStatus();

  const submit = async () => {
    let values: IAAReportCreateValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    try {
      setSubmitting(true);
      const report = await annotationApi.createIAAReport(projectId, {
        task_id: taskId,
        metric_definitions_version: values.metric_definitions_version.trim(),
        iou_threshold: values.iou_threshold,
      });
      if (report.status === "INSUFFICIENT_DATA") {
        messageApi.warning(
          "IAA 已计算，但重叠样本不足（INSUFFICIENT_DATA）：指标显示 N/A，不是 0。",
        );
      } else {
        messageApi.success("IAA 报告已生成。");
      }
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isForbidden) {
        messageApi.error("当前账号没有计算 IAA 的权限（403）。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "IAA 计算失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="计算 IAA 报告"
        open={open}
        okText="开始计算"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !online }}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        {!online ? (
          <Typography.Paragraph type="warning">
            当前处于离线状态，无法提交计算请求。
          </Typography.Paragraph>
        ) : null}
        <Form
          form={form}
          layout="vertical"
          requiredMark
          initialValues={{
            metric_definitions_version: defaultMetricDefinitionsVersion ?? "",
            iou_threshold: 0.5,
          }}
        >
          <Form.Item
            name="metric_definitions_version"
            label="指标口径版本"
            rules={[
              {
                validator: (_rule, value: string | undefined) => {
                  const error = validateMetricDefinitionsVersion(value);
                  return error === null
                    ? Promise.resolve()
                    : Promise.reject(new Error(error));
                },
              },
            ]}
            extra="契约只要求 1–64 字符；请填写团队约定的口径版本，报告会记录本次使用的版本。"
          >
            <Input maxLength={METRIC_DEFINITIONS_VERSION_MAX} />
          </Form.Item>
          <Form.Item
            name="iou_threshold"
            label="IoU 阈值"
            rules={[
              {
                validator: (_rule, value: number | null | undefined) =>
                  validateIouThreshold(value) === null
                    ? Promise.resolve()
                    : Promise.reject(new Error(validateIouThreshold(value) ?? "")),
              },
            ]}
            extra="用于 Span F1 的匹配阈值，契约默认 0.5。"
          >
            <InputNumber
              min={0}
              max={1}
              step={0.05}
              style={{ width: "100%" }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
