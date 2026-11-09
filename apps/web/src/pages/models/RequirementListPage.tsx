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
import { modelOpsApi, type RequirementCreateInput } from "../../services/modelops";
import {
  presentDimensionType,
  presentPriority,
  presentRequirementStatus,
  presentSourceType,
} from "../../services/modelopsPresentation";
import { requirementDetailPath } from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  GAP_DIMENSION_VALUES,
  REQUIREMENT_STATUS_VALUES,
  type DataRequirement,
} from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SOURCE_TYPES = ["MANUAL", "MODEL_FEEDBACK", "EVALUATION", "OPERATION"];

function CreateRequirementModal({
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
  const [form] = Form.useForm<RequirementCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.createRequirement(projectId, {
        ...values,
        target_episode_count: values.target_episode_count ?? 1,
        priority: values.priority ?? "P1",
        quality_thresholds: values.quality_thresholds ?? {},
        dimensions: values.dimensions.map((item) => ({
          dimension_type: item.dimension_type,
          dimension_value: item.dimension_value,
          current_count: item.current_count ?? null,
          target_count: item.target_count ?? null,
        })),
      });
      messageApi.success("数据需求已创建（DRAFT）");
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
        title="新建数据需求"
        open={open}
        width={680}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Space style={{ display: "flex" }} align="start" wrap>
            <Form.Item
              name="code"
              label="需求编码"
              rules={[{ required: true, max: 64, message: "请输入 1-64 字符编码" }]}
            >
              <Input style={{ width: 240 }} placeholder="DR-KITCHEN-001" />
            </Form.Item>
            <Form.Item name="priority" label="优先级" initialValue="P1">
              <Select
                style={{ width: 160 }}
                options={["P0", "P1", "P2", "P3"].map((value) => ({
                  value,
                  label: presentPriority(value).label,
                }))}
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="title"
            label="标题"
            rules={[{ required: true, max: 200, message: "请输入标题" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Space style={{ display: "flex" }} align="start" wrap>
            <Form.Item
              name="source_type"
              label="来源"
              initialValue="MANUAL"
              rules={[{ required: true }]}
            >
              <Select
                style={{ width: 180 }}
                options={SOURCE_TYPES.map((value) => ({
                  value,
                  label: presentSourceType(value),
                }))}
              />
            </Form.Item>
            <Form.Item
              name="target_episode_count"
              label="目标 Episode 数"
              initialValue={1}
              rules={[{ required: true }]}
            >
              <InputNumber min={1} style={{ width: 160 }} />
            </Form.Item>
          </Space>
          <Form.Item
            name="owner_id"
            label="负责人用户 ID（UUID）"
            rules={[
              { required: true, message: "请输入负责人 UUID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
          <Typography.Text strong>缺口维度</Typography.Text>
          <Form.List name="dimensions" initialValue={[{ dimension_type: "SCENE" }]}>
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 8 }}>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="start" wrap>
                    <Form.Item
                      {...rest}
                      name={[name, "dimension_type"]}
                      rules={[{ required: true }]}
                    >
                      <Select
                        style={{ width: 140 }}
                        options={GAP_DIMENSION_VALUES.map((value) => ({
                          value,
                          label: presentDimensionType(value),
                        }))}
                      />
                    </Form.Item>
                    <Form.Item
                      {...rest}
                      name={[name, "dimension_value"]}
                      rules={[{ required: true, message: "维度值" }]}
                    >
                      <Input placeholder="kitchen" style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "current_count"]}>
                      <InputNumber placeholder="当前" min={0} style={{ width: 100 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "target_count"]}>
                      <InputNumber placeholder="目标" min={0} style={{ width: 100 }} />
                    </Form.Item>
                    <Button type="text" icon={<MinusCircleOutlined />} onClick={() => remove(name)} />
                  </Space>
                ))}
                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add()}>
                  增加维度
                </Button>
              </Space>
            )}
          </Form.List>
          <Form.Item name="due_at" label="期望完成时间（可选）" style={{ marginTop: 16 }}>
            <Input placeholder="2026-12-01T00:00:00Z" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function RequirementListContent({
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
      modelOpsApi.listRequirements(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.requirement_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<DataRequirement> = [
    {
      title: "标题 / 编码",
      key: "title",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={requirementDetailPath(item.requirement_id)}>{item.title}</Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentRequirementStatus} />
      ),
    },
    {
      title: "优先级",
      dataIndex: "priority",
      width: 100,
      render: (value: string) => <StatusTag value={value} presentation={presentPriority} />,
    },
    {
      title: "来源",
      dataIndex: "source_type",
      width: 120,
      render: (value: string) => presentSourceType(value),
    },
    {
      title: "目标 Episode",
      dataIndex: "target_episode_count",
      width: 120,
    },
    {
      title: "截止时间",
      dataIndex: "due_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="数据需求"
      description="模型反馈、评测结论与人工登记汇入数据需求；满足与关闭需由服务端状态机与权限校验。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={REQUIREMENT_STATUS_VALUES.map((value) => ({
              value,
              label: presentRequirementStatus(value).label,
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
            新建需求
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
      <Table<DataRequirement>
        rowKey="requirement_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无数据需求" }}
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
      <CreateRequirementModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function RequirementListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="数据需求" description="数据需求必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据需求列表" />
      </PageContainer>
    );
  }
  return (
    <RequirementListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
