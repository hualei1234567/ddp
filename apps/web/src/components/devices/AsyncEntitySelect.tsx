import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

export interface EntityOption {
  value: string;
  label: string;
  /** 补充说明，例如版本号或业务编码。 */
  hint?: string;
  /** 选中后可带出的冻结版本（SOP / 动作库）。 */
  version?: string;
}

export interface AsyncEntitySelectProps {
  /** 中文实体名，用于加载失败与空态文案。 */
  entityLabel: string;
  placeholder: string;
  /**
   * 选项加载器。请求失败时必须抛出 `ApiError`/Error，组件会展示失败原因并
   * 提供重试，绝不把失败伪装成「没有数据」。
   */
  loadOptions: (signal: AbortSignal) => Promise<EntityOption[]>;
  value?: string | null;
  onChange?: (value: string | undefined, option?: EntityOption) => void;
  /**
   * 选中项变化时的附加回调。
   *
   * 与 `onChange` 分开的原因：作为 `Form.Item` 的直接子节点时，表单会覆盖
   * `onChange`（只保留取值），需要额外通知父组件「选中了哪一行、版本号是多少」。
   */
  onOptionChange?: (option?: EntityOption) => void;
  allowClear?: boolean;
  disabled?: boolean;
}

function describeError(caught: unknown): string {
  return caught instanceof Error ? caught.message : "选项加载失败";
}

/**
 * 关联实体下拉选择器。
 *
 * 采集任务、设备、传感器与机器人表单里的关联项都用它，避免让用户手填 UUID；
 * 选项来自各自域的真实列表接口（分页上限 100，超出时提示先缩小范围）。
 * 组件只负责取值与展示，不推断业务可用性——服务端仍会校验引用是否同项目存在。
 */
export default function AsyncEntitySelect({
  entityLabel,
  placeholder,
  loadOptions,
  value,
  onChange,
  onOptionChange,
  allowClear = true,
  disabled = false,
}: AsyncEntitySelectProps) {
  const [options, setOptions] = useState<EntityOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const loadRef = useRef(loadOptions);
  loadRef.current = loadOptions;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError(null);
    loadRef
      .current(controller.signal)
      .then((loaded) => {
        if (!active || controller.signal.aborted) {
          return;
        }
        setOptions(loaded);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (!active || controller.signal.aborted) {
          return;
        }
        setOptions([]);
        setLoading(false);
        setError(describeError(caught));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [reloadToken]);

  const selectOptions = options.map((option) => ({
    value: option.value,
    label: option.hint ? `${option.label} · ${option.hint}` : option.label,
  }));

  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Select
        value={value ?? undefined}
        options={selectOptions}
        placeholder={placeholder}
        loading={loading}
        disabled={disabled}
        allowClear={allowClear}
        showSearch
        optionFilterProp="label"
        status={error ? "error" : undefined}
        style={{ width: "100%" }}
        notFoundContent={
          loading
            ? "正在加载…"
            : error
              ? "加载失败，见下方提示"
              : `暂无可用${entityLabel}`
        }
        onChange={(next) => {
          const matched = options.find((option) => option.value === next);
          onChange?.(next, matched);
          onOptionChange?.(matched);
        }}
      />
      {error ? (
        <Space size={8} wrap>
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            {entityLabel}加载失败：{error}
          </Typography.Text>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => setReloadToken((token) => token + 1)}
          >
            重试
          </Button>
        </Space>
      ) : null}
    </Space>
  );
}
