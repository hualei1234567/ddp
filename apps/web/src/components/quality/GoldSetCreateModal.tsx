import {
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import { useCallback, useState } from "react";

import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useRequest } from "../../hooks/useRequest";
import { ApiError } from "../../services/api";
import { annotationApi } from "../../services/annotation";
import {
  GOLD_SET_CODE_MAX,
  GOLD_SET_NAME_MAX,
  parseObjectArrayJson,
  parseObjectJson,
  validateExposurePolicy,
  validateGoldSetCode,
  validateGoldSetItems,
  validateGoldSetName,
  validateMaxExposure,
  validateRotationPolicy,
} from "../../services/qualityForm";
import {
  GOLD_EXPOSURE_POLICY_VALUES,
  GOLD_ROTATION_POLICY_VALUES,
  type AnnotationSchema,
} from "../../types/annotation";
import type { GoldSetCreateInput } from "../../types/quality";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

interface GoldSetCreateValues {
  code: string;
  name?: string;
  schema_id: string;
  items: string;
  usage_policy?: string;
  exposure_policy: string;
  max_exposure_per_annotator: number;
  rotation_policy: string;
}

interface GoldSetCreateModalProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
  /** 创建成功后由页面刷新列表。 */
  onCreated: () => void;
}

function jsonRule(
  parse: (text: string, label: string) => unknown,
  label: string,
) {
  return {
    validator: (_: unknown, value: string | undefined) => {
      try {
        parse(value ?? "", label);
        return Promise.resolve();
      } catch (error) {
        return Promise.reject(
          error instanceof Error ? error : new Error("格式不正确"),
        );
      }
    },
  };
}

/**
 * 黄金集创建表单（初始 DRAFT）。
 *
 * 依据契约 `GoldSetCreate`（`schemas.stage3.yaml`）：
 * - `code` / `schema_id` / `items` 必填，其余可选；
 * - `items` 只声明为「对象数组、至少 1 项」，逐项字段契约未声明，
 *   因此用 JSON 文本录入并只校验这一层，不在前端猜 `episode_id` /
 *   `expected_labels` 的结构（写错时由服务端 422 明确告知）。
 * - `schema_version` / `schema_sha256` 不提交：由服务端按当前已发布版本校验。
 */
export default function GoldSetCreateModal({
  projectId,
  open,
  onClose,
  onCreated,
}: GoldSetCreateModalProps) {
  const [form] = Form.useForm<GoldSetCreateValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const online = useOnlineStatus();

  const loadSchemas = useCallback(
    async (signal: AbortSignal) => {
      const page = await annotationApi.listSchemas(projectId, { signal });
      // 黄金集只能引用已发布 Schema（服务端强校验），这里只列可选项。
      return page.items.filter((item) => item.status === "PUBLISHED");
    },
    [projectId],
  );
  const {
    data: schemas,
    error,
    loading,
    reload,
  } = useRequest(loadSchemas);

  const submit = async () => {
    let values: GoldSetCreateValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    let input: GoldSetCreateInput;
    try {
      input = {
        code: values.code.trim(),
        name: values.name?.trim() ? values.name.trim() : null,
        schema_id: values.schema_id,
        items: parseObjectArrayJson(values.items, "黄金项"),
        usage_policy: parseObjectJson(values.usage_policy ?? "", "使用策略"),
        exposure_policy: values.exposure_policy,
        max_exposure_per_annotator: values.max_exposure_per_annotator,
        rotation_policy: values.rotation_policy,
      };
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "表单内容格式不正确。",
      );
      return;
    }

    try {
      setSubmitting(true);
      await annotationApi.createGoldSet(projectId, input);
      messageApi.success("黄金集已创建，初始状态为草稿（DRAFT）");
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isForbidden) {
        messageApi.error("当前账号没有创建黄金集的权限（403）。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "创建失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const publishedSchemas: AnnotationSchema[] = schemas ?? [];
  const cannotCreateReason =
    schemas !== null && publishedSchemas.length === 0
      ? "没有可用的已发布 Schema；黄金集只能引用 PUBLISHED Schema，请先发布一个 Schema。"
      : null;

  return (
    <>
      {contextHolder}
      <Modal
        title="新建黄金集"
        open={open}
        okText="创建"
        cancelText="取消"
        width={720}
        confirmLoading={submitting}
        okButtonProps={{
          disabled: !online || cannotCreateReason !== null || loading,
        }}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        {!online ? (
          <Typography.Paragraph type="warning">
            当前处于离线状态，无法提交创建请求。
          </Typography.Paragraph>
        ) : null}
        {loading ? (
          <PageState compact variant="loading" title="正在读取已发布 Schema" />
        ) : error ? (
          <ApiErrorState error={error} compact onRetry={reload} />
        ) : null}
        {cannotCreateReason ? (
          <Typography.Paragraph type="warning">
            {cannotCreateReason}
          </Typography.Paragraph>
        ) : null}
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="编码"
            rules={[
              {
                validator: (_rule, value: string | undefined) => {
                  const error = validateGoldSetCode(value);
                  return error === null
                    ? Promise.resolve()
                    : Promise.reject(new Error(error));
                },
              },
            ]}
          >
            <Input maxLength={GOLD_SET_CODE_MAX} placeholder="项目内唯一业务标识" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[
              {
                validator: (_rule, value: string | undefined) => {
                  const error = validateGoldSetName(value);
                  return error === null
                    ? Promise.resolve()
                    : Promise.reject(new Error(error));
                },
              },
            ]}
          >
            <Input maxLength={GOLD_SET_NAME_MAX} placeholder="可选，最多 200 字符" />
          </Form.Item>
          <Form.Item
            name="schema_id"
            label="标注 Schema"
            rules={[{ required: true, message: "请选择已发布 Schema" }]}
            extra="只列出 PUBLISHED Schema；Schema 版本与 Hash 由服务端按当前发布版本冻结。"
          >
            <Select
              disabled={publishedSchemas.length === 0}
              options={publishedSchemas.map((schema) => ({
                value: schema.schema_id,
                label: `${schema.code} · ${schema.name} · v${schema.schema_version}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="items"
            label="黄金项（JSON 对象数组）"
            initialValue="[]"
            extra="契约只声明「对象数组且至少 1 项」；每项的字段结构由服务端校验，请按后端要求填写。"
            rules={[
              jsonRule(
                (text, label) => {
                  const items = parseObjectArrayJson(text, label);
                  const error = validateGoldSetItems(items);
                  if (error !== null) {
                    throw new Error(error);
                  }
                  return items;
                },
                "黄金项",
              ),
            ]}
          >
            <Input.TextArea rows={6} spellCheck={false} />
          </Form.Item>
          <Space wrap size={16}>
            <Form.Item
              name="exposure_policy"
              label="曝光策略"
              initialValue="HIDDEN"
              rules={[
                {
                  validator: (_rule, value: string) =>
                    validateExposurePolicy(value) === null
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(validateExposurePolicy(value) ?? ""),
                        ),
                },
              ]}
            >
              <Select
                style={{ width: 160 }}
                options={GOLD_EXPOSURE_POLICY_VALUES.map((value) => ({
                  value,
                  label: value,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="max_exposure_per_annotator"
              label="每人曝光上限"
              initialValue={1}
              rules={[
                {
                  validator: (_rule, value: number | null | undefined) => {
                    const error = validateMaxExposure(value);
                    return error === null
                      ? Promise.resolve()
                      : Promise.reject(new Error(error));
                  },
                },
              ]}
            >
              <InputNumber min={1} precision={0} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item
              name="rotation_policy"
              label="轮换策略"
              initialValue="NONE"
              rules={[
                {
                  validator: (_rule, value: string) =>
                    validateRotationPolicy(value) === null
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(validateRotationPolicy(value) ?? ""),
                        ),
                },
              ]}
            >
              <Select
                style={{ width: 180 }}
                options={GOLD_ROTATION_POLICY_VALUES.map((value) => ({
                  value,
                  label: value,
                }))}
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="usage_policy"
            label="使用策略（JSON 对象）"
            initialValue="{}"
            extra="可选；留空表示无额外约束。"
            rules={[jsonRule(parseObjectJson, "使用策略")]}
          >
            <Input.TextArea rows={3} spellCheck={false} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
