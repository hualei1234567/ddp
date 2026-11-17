import {
  Alert,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import { useCallback, useMemo, useState } from "react";

import AsyncEntitySelect, {
  type EntityOption,
} from "../devices/AsyncEntitySelect";
import { getPriority } from "../../services/collectionPresentation";
import {
  buildTaskCreateInput,
  type TaskCreateFormValues,
} from "../../services/collectionTaskForm";
import { assetId } from "../../services/collectionAssetPresentation";
import { collectionAssetApi } from "../../services/collectionAssets";
import { collectionsApi } from "../../services/collections";
import { locationApi } from "../../services/locations";
import {
  COLLECTION_PRIORITY_VALUES,
  type CollectionPriority,
} from "../../types/collection";
import type { CollectionAssetKind } from "../../types/collectionAsset";

const PRIORITY_OPTIONS = COLLECTION_PRIORITY_VALUES.map((value) => ({
  value,
  label: getPriority(value).label,
}));

interface CreateTaskModalProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

interface TaskAssetOption extends EntityOption {
  version?: string;
}

function createAssetLoader(
  projectId: string,
  kind: CollectionAssetKind,
): (signal: AbortSignal) => Promise<TaskAssetOption[]> {
  return async (signal) => {
    const page = await collectionAssetApi.list(kind, projectId, { signal });
    return page.items.map((asset) => ({
      value: assetId(asset),
      label: asset.name,
      hint: asset.code,
      version:
        kind === "sops"
          ? (asset as { sop_version?: string }).sop_version
          : kind === "action-specs"
            ? (asset as { spec_version?: string }).spec_version
            : undefined,
    }));
  };
}

/**
 * 新建采集任务。
 *
 * 关联项全部用下拉选择器（素材 / 场地走真实列表接口），不让用户手填 UUID；
 * SOP 与动作库的版本号由选中行带出，因为服务端要求 `id` 与 `version` 成对。
 * 服务端仍会校验引用是否同项目存在、版本是否一致。
 */
export default function CreateTaskModal({
  projectId,
  open,
  onClose,
  onCreated,
}: CreateTaskModalProps) {
  const [form] = Form.useForm<TaskCreateFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [sopVersion, setSopVersion] = useState<string | null>(null);
  const [actionSpecVersion, setActionSpecVersion] = useState<string | null>(
    null,
  );
  const [messageApi, contextHolder] = message.useMessage();

  const loaders = useMemo(
    () => ({
      plans: createAssetLoader(projectId, "plans"),
      sops: createAssetLoader(projectId, "sops"),
      scenarios: createAssetLoader(projectId, "scenarios"),
      actionSpecs: createAssetLoader(projectId, "action-specs"),
      locations: async (signal: AbortSignal): Promise<EntityOption[]> => {
        const page = await locationApi.list(projectId, { signal });
        return page.items.map((location) => ({
          value: location.location_id,
          label: location.name,
          hint: `${location.code} · ${location.kind}`,
        }));
      },
    }),
    [projectId],
  );

  const resetForm = useCallback(() => {
    form.resetFields();
    setSopVersion(null);
    setActionSpecVersion(null);
  }, [form]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const input = buildTaskCreateInput(values, {
        sop_version: sopVersion,
        action_spec_version: actionSpecVersion,
      });
      setSubmitting(true);
      await collectionsApi.createTask(projectId, input);
      messageApi.success("采集任务已创建");
      resetForm();
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
        title="新建采集任务"
        open={open}
        width={720}
        okText="创建任务"
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void submit()}
        onCancel={onClose}
        afterClose={resetForm}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="关联项从项目内已有素材中选择"
          description="任务创建后状态为草稿；SOP 与动作库会冻结当前版本。只列出已加载的前 100 项，选择器内可搜索。"
        />
        <Form
          form={form}
          layout="vertical"
          requiredMark
          initialValues={{
            priority: "P1" as CollectionPriority,
            target_episode_count: 0,
            target_duration_sec: 0,
          }}
        >
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="code"
              label="编码"
              rules={[{ required: true, max: 64 }]}
              style={{ flex: 1 }}
            >
              <Input placeholder="COL-2026-001" />
            </Form.Item>
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, max: 200 }]}
              style={{ flex: 2 }}
            >
              <Input placeholder="厨房桌面抓取采集" />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="priority"
              label="优先级"
              rules={[{ required: true }]}
              style={{ width: 160 }}
            >
              <Select options={PRIORITY_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="target_episode_count"
              label="目标条数"
              rules={[{ required: true }]}
              style={{ width: 180 }}
            >
              <InputNumber min={0} precision={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="target_duration_sec"
              label="目标时长"
              rules={[{ required: true }]}
              style={{ width: 220 }}
            >
              <InputNumber
                min={0}
                precision={0}
                addonAfter="秒"
                style={{ width: "100%" }}
              />
            </Form.Item>
          </Space>
          <Form.Item name="plan_id" label="采集方案">
            <AsyncEntitySelect
              entityLabel="采集方案"
              placeholder="选择已登记的采集方案"
              loadOptions={loaders.plans}
            />
          </Form.Item>
          <Form.Item
            name="sop_id"
            label="SOP"
            extra="选中后自动带上该 SOP 的版本号；服务端要求 ID 与版本成对。"
          >
            <AsyncEntitySelect
              entityLabel="SOP"
              placeholder="选择已登记的 SOP"
              loadOptions={loaders.sops}
              onOptionChange={(option) =>
                setSopVersion(option?.version ?? null)
              }
            />
          </Form.Item>
          <Form.Item name="scenario_id" label="场景">
            <AsyncEntitySelect
              entityLabel="场景"
              placeholder="选择已登记的场景"
              loadOptions={loaders.scenarios}
            />
          </Form.Item>
          <Form.Item
            name="action_spec_id"
            label="动作库"
            extra="选中后自动带上该动作库的版本号。"
          >
            <AsyncEntitySelect
              entityLabel="动作库"
              placeholder="选择已登记的动作库"
              loadOptions={loaders.actionSpecs}
              onOptionChange={(option) =>
                setActionSpecVersion(option?.version ?? null)
              }
            />
          </Form.Item>
          <Form.Item name="location_id" label="场地">
            <AsyncEntitySelect
              entityLabel="场地"
              placeholder="选择基地 / 房间 / 实验室"
              loadOptions={loaders.locations}
            />
          </Form.Item>
          <Typography.Text type="secondary">
            未选择的关联项不会提交；任务状态、项目归属与审计字段由服务端维护。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  );
}
