import { Alert, Button, Card, Form, Input, Select, Space, Typography } from "antd";
import { useState } from "react";

import type {
  GoldEvaluationCreateInput,
  GoldInjectionCreateInput,
} from "../../services/annotationGoldSetApi";
import type {
  GoldEvaluation,
  GoldInjection,
} from "../../services/annotationQualityParsers";
import type { GoldSet } from "../../types/annotation";

interface EvaluateFormValues {
  task_id: string;
  submission_id: string;
}

interface InjectFormValues {
  task_id: string;
  annotator_id: string;
  item_ids: string[];
}

interface GoldEvaluationPanelProps {
  goldSet: GoldSet;
  evaluation: GoldEvaluation | null;
  injection: GoldInjection | null;
  error: string | null;
  onEvaluate: (input: GoldEvaluationCreateInput) => Promise<void>;
  onInject: (input: GoldInjectionCreateInput) => Promise<void>;
}

function formatAccuracy(value: number | null): string {
  return value === null ? "无可评估项（不是 0%）" : `${(value * 100).toFixed(1)}%`;
}

/**
 * 黄金集的评估与注入入口。
 *
 * - 评估：拿黄金集期望答案对一条提交打分；
 * - 注入：把黄金项注入给指定标注员，用于盲测与质量控制。
 *
 * 两个动作都以服务端记录为准；失败原因（无权限 / 答案缺失 / 状态不允许）
 * 直接用后端 `error.message` 展示。
 */
export default function GoldEvaluationPanel({
  goldSet,
  evaluation,
  injection,
  error,
  onEvaluate,
  onInject,
}: GoldEvaluationPanelProps) {
  const [evaluateForm] = Form.useForm<EvaluateFormValues>();
  const [injectForm] = Form.useForm<InjectFormValues>();
  const [busy, setBusy] = useState<"evaluate" | "inject" | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const runEvaluate = async () => {
    let values: EvaluateFormValues;
    try {
      values = await evaluateForm.validateFields();
    } catch {
      return;
    }
    setBusy("evaluate");
    setLocalError(null);
    try {
      await onEvaluate({
        task_id: values.task_id.trim(),
        submission_id: values.submission_id.trim(),
      });
    } catch (caught) {
      setLocalError(
        caught instanceof Error ? caught.message : "黄金集评估失败。",
      );
    } finally {
      setBusy(null);
    }
  };

  const runInject = async () => {
    let values: InjectFormValues;
    try {
      values = await injectForm.validateFields();
    } catch {
      return;
    }
    setBusy("inject");
    setLocalError(null);
    try {
      await onInject({
        task_id: values.task_id.trim(),
        annotator_id: values.annotator_id.trim(),
        item_ids: values.item_ids,
      });
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : "黄金项注入失败。");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Typography.Text strong>评估与注入</Typography.Text>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {localError ? <Alert type="error" showIcon message={localError} /> : null}
      <Card
        size="small"
        title="用黄金集评估提交"
        extra={
          <Button type="primary" loading={busy === "evaluate"} onClick={() => void runEvaluate()}>
            执行评估
          </Button>
        }
      >
        <Form form={evaluateForm} layout="inline">
          <Form.Item
            name="task_id"
            label="任务 ID"
            rules={[{ required: true, message: "请填写任务 ID" }]}
          >
            <Input placeholder="任务 UUID" style={{ width: 320 }} />
          </Form.Item>
          <Form.Item
            name="submission_id"
            label="提交 ID"
            rules={[{ required: true, message: "请填写提交 ID" }]}
          >
            <Input placeholder="提交 UUID" style={{ width: 320 }} />
          </Form.Item>
        </Form>
        {evaluation ? (
          <Space direction="vertical" size={4}>
            <Typography.Text>
              评估项 {evaluation.evaluated_count} · 通过 {evaluation.passed_count} · 样本{" "}
              {evaluation.sample_size} · 准确率 {formatAccuracy(evaluation.accuracy)}
            </Typography.Text>
            <Typography.Text type="secondary">
              状态 {evaluation.status} · 计算时间 {evaluation.computed_at}
            </Typography.Text>
          </Space>
        ) : null}
      </Card>
      <Card
        size="small"
        title="注入黄金项"
        extra={
          <Button type="primary" loading={busy === "inject"} onClick={() => void runInject()}>
            执行注入
          </Button>
        }
      >
        <Form form={injectForm} layout="inline">
          <Form.Item
            name="task_id"
            label="任务 ID"
            rules={[{ required: true, message: "请填写任务 ID" }]}
          >
            <Input placeholder="任务 UUID" style={{ width: 320 }} />
          </Form.Item>
          <Form.Item
            name="annotator_id"
            label="标注员 ID"
            rules={[{ required: true, message: "请填写标注员 ID" }]}
          >
            <Input placeholder="标注员 UUID" style={{ width: 320 }} />
          </Form.Item>
          <Form.Item
            name="item_ids"
            label="黄金项"
            rules={[{ required: true, message: "请选择至少一个黄金项" }]}
          >
            <Select
              mode="multiple"
              style={{ minWidth: 320 }}
              options={goldSet.items.map((item) => ({
                value: item.gold_item_id,
                label: `${item.gold_item_id} · ${item.difficulty}`,
              }))}
            />
          </Form.Item>
        </Form>
        {injection ? (
          <Typography.Text>
            已注入 {injection.items.length} 项，生成曝光记录 {injection.exposure_ids.length} 条。
          </Typography.Text>
        ) : null}
      </Card>
      <Typography.Text type="secondary">
        注入与评估都会写入审计；盲测黄金集不会向标注员暴露期望答案。
      </Typography.Text>
    </Space>
  );
}
