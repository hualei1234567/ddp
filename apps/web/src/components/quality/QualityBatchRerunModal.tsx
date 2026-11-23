import { Alert, Button, Checkbox, Modal, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useMemo, useState } from "react";

import { formatDateTime } from "../../services/format";
import {
  filterFailedSubjects,
  type QualityRerunSubject,
} from "../../services/qualityRerunPresentation";
import type { QueuedQualityRun } from "../../types/qualityRun";

export type { QualityRerunSubject } from "../../services/qualityRerunPresentation";

/**
 * 批量重跑确认对话框（V31-033 / FR-026 + FR-028）。
 *
 * 四步流程中的「预览 → 确认 → 执行回执」都收在对话框里：
 * 1. 预览：只统计**将入队的数量**，不预测、不展示任何结论；
 * 2. 确认：调用方注入的 `rerun`（页面负责换成 batch API 调用）；
 * 3. 回执：渲染 `QueuedQualityRun` 列表——**排队回执不是质检结论**，
 *    文案与结构上都不给「检查通过」的暗示（ADR-0045 语义红线）。
 *
 * FR-028「只重试失败项」是同一对话框上的筛选开关：勾选后只把上一次
 * 运行结论为 FAILED 的对象交给 `rerun`，仍走同一 batch API。
 */
export default function QualityBatchRerunModal({
  open,
  subjects,
  scopeLabel,
  rerun,
  onClose,
  defaultOnlyFailed = false,
}: {
  open: boolean;
  /** 本次可重跑对象范围（含最近一次运行结论，供 FR-028 筛选）。 */
  subjects: QualityRerunSubject[];
  /** 范围说明（如「已选择的 Episode」），原样展示给用户。 */
  scopeLabel: string;
  /** 确认后的入队调用；入参是去重后的对象 ID 列表。 */
  rerun: (subjectIds: string[]) => Promise<QueuedQualityRun[]>;
  onClose: () => void;
  /** 打开时预选「只重试失败项」（FR-028：失败项重试入口）。 */
  defaultOnlyFailed?: boolean;
}) {
  const [phase, setPhase] = useState<"preview" | "submitting" | "receipt">(
    "preview",
  );
  const [receipt, setReceipt] = useState<QueuedQualityRun[] | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [onlyFailed, setOnlyFailed] = useState(defaultOnlyFailed);

  const hasLastRunInfo = subjects.some((item) => item.lastStatus !== null);
  const effectiveSubjects = useMemo(
    () => (onlyFailed && hasLastRunInfo ? filterFailedSubjects(subjects) : subjects),
    [onlyFailed, hasLastRunInfo, subjects],
  );
  const effectiveIds = effectiveSubjects.map((item) => item.subjectId);
  const failedUnavailable = onlyFailed && effectiveSubjects.length === 0;

  const confirm = async () => {
    if (effectiveIds.length === 0) {
      return;
    }
    setPhase("submitting");
    setErrorText(null);
    try {
      const queued = await rerun(effectiveIds);
      setReceipt(queued);
      setPhase("receipt");
    } catch (caught) {
      setErrorText(
        caught instanceof Error && caught.message !== ""
          ? caught.message
          : "服务未返回失败原因，请稍后重试。",
      );
      // 回到预览：用户可以修正后再次确认，不丢已选范围。
      setPhase("preview");
    }
  };

  const reset = () => {
    setPhase("preview");
    setReceipt(null);
    setErrorText(null);
    setOnlyFailed(false);
    onClose();
  };

  const receiptColumns: TableColumnsType<QueuedQualityRun> = [
    {
      title: "运行 ID",
      dataIndex: "run_id",
      width: 300,
      render: (value: string) => (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    {
      title: "Episode",
      dataIndex: "subject_id",
      width: 300,
      render: (value: string) => (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    {
      title: "生命周期",
      dataIndex: "lifecycle",
      width: 110,
      render: (value: QueuedQualityRun["lifecycle"]) => (
        <Tag color="blue">{value}</Tag>
      ),
    },
    {
      title: "入队时间",
      dataIndex: "created_at",
      width: 170,
      render: (value: string) => formatDateTime(value),
    },
  ];

  return (
    <Modal
      title="批量重跑质检"
      open={open}
      onCancel={reset}
      footer={
        phase === "receipt" ? (
          <Button onClick={reset}>关闭</Button>
        ) : (
          <Space>
            <Button onClick={reset}>取消</Button>
            <Button
              type="primary"
              danger
              loading={phase === "submitting"}
              disabled={effectiveIds.length === 0}
              onClick={() => void confirm()}
            >
              确认重跑
            </Button>
          </Space>
        )
      }
      width={760}
    >
      {phase === "receipt" && receipt !== null ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message="排队回执不是质检结论"
            description="已为所选对象各入队一次质检（QUEUED），由质检 Worker 领取执行。已排队 ≠ 已通过；完成后请在对应对象页面查看结论与差异报告。"
          />
          <Typography.Title level={5} style={{ margin: 0 }}>
            已排队 {receipt.length} 条（尚无质检结论）
          </Typography.Title>
          <Table<QueuedQualityRun>
            rowKey="run_id"
            size="small"
            columns={receiptColumns}
            dataSource={receipt}
            pagination={false}
            scroll={{ x: 880 }}
          />
        </Space>
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {errorText !== null ? (
            <Alert
              type="error"
              showIcon
              message="重跑入队失败"
              description={errorText}
            />
          ) : null}
          <Alert
            type="info"
            showIcon
            message={`将重跑 ${effectiveSubjects.length} 条 Episode`}
            description={`范围：${scopeLabel}。确认后为每个对象各入队一次质检，由质检 Worker 领取执行；规则版本以服务端入队时固化为准。`}
          />
          {hasLastRunInfo ? (
            <Space direction="vertical" size={4}>
              <Checkbox
                checked={onlyFailed}
                onChange={(event) => setOnlyFailed(event.target.checked)}
              >
                只重试失败项
              </Checkbox>
              <Typography.Text type="secondary">
                {failedUnavailable
                  ? "上一次运行没有结论为 FAILED 的对象；可关闭开关重跑全部所选对象。"
                  : "勾选后仅重跑上一次运行结论为 FAILED 的对象；无结论（INCOMPLETE）与未质检对象不会进入重试列表。"}
              </Typography.Text>
            </Space>
          ) : null}
          <Typography.Text type="secondary">
            预览只统计入队数量；执行结果以 Worker 完成后的运行记录为准。
          </Typography.Text>
        </Space>
      )}
    </Modal>
  );
}
