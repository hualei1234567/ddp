import { MinusCircleOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { modelOpsApi, type EvaluationCreateInput } from "../../services/modelops";
import {
  presentEvaluationStatus,
  presentVerdict,
} from "../../services/modelopsPresentation";
import { evaluationDetailPath } from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import { EVALUATION_STATUS_VALUES, type Evaluation } from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface ThresholdFormRow {
  name: string;
  min?: number | null;
  max?: number | null;
}

interface EvaluationFormValues {
  model_version_id: string;
  dataset_version_id: string;
  baseline_evaluation_id?: string;
  adapter_id?: string;
  model_object_key?: string;
  benchmarks: ThresholdFormRow[];
}

function CreateEvaluationModal({
  projectId,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<EvaluationFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const thresholds: EvaluationCreateInput["thresholds"] = {};
      for (const row of values.benchmarks) {
        thresholds[row.name.trim()] = {
          min: row.min ?? null,
          max: row.max ?? null,
        };
      }
      if (Object.keys(thresholds).length === 0) {
        messageApi.warning("至少需要一项评测阈值，否则评测结论只能是 UNKNOWN。");
        return;
      }
      setSubmitting(true);
      const objectKey = values.model_object_key?.trim();
      await modelOpsApi.createEvaluation(projectId, {
        model_version_id: values.model_version_id,
        dataset_version_id: values.dataset_version_id,
        thresholds,
        baseline_evaluation_id: values.baseline_evaluation_id ?? null,
        // 指定 adapter 才会真的提交到执行侧；不指定时评测停在 QUEUED，
        // 这是如实状态而不是"自动完成"。
        adapter_id: values.adapter_id?.trim() || null,
        config: objectKey ? { model_object_key: objectKey } : {},
        metrics: {},
      });
      messageApi.success(
        values.adapter_id?.trim()
          ? "评测已提交适配器，结论由回调回写。"
          : "评测已入队（QUEUED）；未指定适配器，不会自动执行。",
      );
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      if (caught instanceof Error) {
        messageApi.error(caught.message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="发起评测"
        open={open}
        width={640}
        okText="入队"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="model_version_id"
            label="模型版本 ID（UUID）"
            rules={[
              { required: true, message: "请输入模型版本 ID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input placeholder="模型版本 ID" />
          </Form.Item>
          <Form.Item
            name="dataset_version_id"
            label="数据集版本 ID（UUID）"
            rules={[
              { required: true, message: "请输入数据集版本 ID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input placeholder="内部冻结数据集版本 ID" />
          </Form.Item>
          <Form.Item
            name="baseline_evaluation_id"
            label="基线评测 ID（可选）"
            rules={[{ pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="adapter_id"
            label="执行适配器（可选）"
            extra="留空则只入队、不执行；填写已登记的适配器 ID 才会真实提交并回调结论。"
          >
            <Input placeholder="例如 local-model" />
          </Form.Item>
          <Form.Item
            name="model_object_key"
            label="模型产物 object_key（可选）"
            extra="项目下只有一个产物时可留空由适配器解析；多个产物时必须指定。"
          >
            <Input placeholder="models/{project}/{run}/model.joblib" />
          </Form.Item>
          <Typography.Text strong>评测指标阈值</Typography.Text>
          <Form.List name="benchmarks" initialValue={[{ name: "" }]}>
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 8 }}>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="start" wrap>
                    <Form.Item
                      {...rest}
                      name={[name, "name"]}
                      rules={[{ required: true, message: "指标名" }]}
                    >
                      <Input placeholder="success_rate" style={{ width: 180 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "min"]}>
                      <InputNumber placeholder="最小值" style={{ width: 120 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "max"]}>
                      <InputNumber placeholder="最大值" style={{ width: 120 }} />
                    </Form.Item>
                    <Button
                      type="text"
                      icon={<MinusCircleOutlined />}
                      onClick={() => remove(name)}
                    />
                  </Space>
                ))}
                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ name: "" })}>
                  增加指标
                </Button>
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>
    </>
  );
}

function EvaluationListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      modelOpsApi.listEvaluations(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.evaluation_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<Evaluation> = [
    {
      title: "评测 ID",
      dataIndex: "evaluation_id",
      width: 220,
      render: (value: string) => <Link to={evaluationDetailPath(value)}>{value}</Link>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentEvaluationStatus} />
      ),
    },
    {
      title: "结论",
      dataIndex: "verdict",
      width: 100,
      render: (value: string) => <StatusTag value={value} presentation={presentVerdict} />,
    },
    {
      title: "模型版本",
      dataIndex: "model_version_id",
      width: 220,
      ellipsis: true,
    },
    {
      title: "指标",
      key: "metrics",
      render: (_, item) =>
        Object.keys(item.metrics).length > 0
          ? Object.entries(item.metrics)
              .map(([key, value]) => `${key}=${value}`)
              .join("，")
          : "—",
    },
    {
      title: "完成时间",
      dataIndex: "finished_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="模型评测"
      description="评测由外部适配器异步执行；本页只负责入队、查看阈值与结论，不伪造评测结果。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={EVALUATION_STATUS_VALUES.map((value) => ({
              value,
              label: presentEvaluationStatus(value).label,
            }))}
            onChange={(value) => {
              const params = new URLSearchParams(searchParams);
              if (value) {
                params.set("status", value);
              } else {
                params.delete("status");
              }
              setSearchParams(params, { replace: true });
            }}
          />
        </Space>
      }
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            发起评测
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<Evaluation>
        rowKey="evaluation_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无评测" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {hasMore ? (
        <Button block style={{ marginTop: 12 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <Typography.Text type="danger">加载更多失败：{loadMoreError.message}</Typography.Text>
      ) : null}
      <CreateEvaluationModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function EvaluationListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="模型评测" description="评测必须按项目隔离展示。">
        <ProjectScopeRequired scope="评测列表" />
      </PageContainer>
    );
  }
  return (
    <EvaluationListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
