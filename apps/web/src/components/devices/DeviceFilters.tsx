import { Button, Input, Select, Typography } from "antd";
import { useEffect, useState } from "react";

interface DeviceFiltersProps {
  keyword: string;
  statusFilter: string;
  typeFilter: string;
  statusOptions: { value: string; label: string }[];
  typeOptions: { value: string; label: string }[];
  /** 已加载条数，用于明确筛选作用域。 */
  loadedCount: number;
  hasMore: boolean;
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onReset: () => void;
}

/**
 * 设备列表筛选。当前契约未提供服务端筛选参数，因此筛选与统计
 * 只作用于已加载数据；这里必须把作用域直接告知用户。
 */
export default function DeviceFilters({
  keyword,
  statusFilter,
  typeFilter,
  statusOptions,
  typeOptions,
  loadedCount,
  hasMore,
  disabled,
  onChange,
  onReset,
}: DeviceFiltersProps) {
  const [searchText, setSearchText] = useState(keyword);

  useEffect(() => {
    setSearchText(keyword);
  }, [keyword]);

  return (
    <>
      <Input.Search
        allowClear
        value={searchText}
        placeholder="搜索已加载设备（名称、编码、序列号或厂商）"
        style={{ width: 320, maxWidth: "100%" }}
        onChange={(event) => setSearchText(event.target.value)}
        onSearch={(value) => onChange("q", value.trim())}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="设备状态（已加载范围）"
        style={{ width: 180 }}
        value={statusFilter || undefined}
        options={statusOptions}
        disabled={disabled}
        onChange={(value) => onChange("status", value ?? "")}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="设备类型（已加载范围）"
        style={{ width: 180 }}
        value={typeFilter || undefined}
        options={typeOptions}
        disabled={disabled}
        onChange={(value) => onChange("device_type", value ?? "")}
      />
      <Button onClick={onReset}>清空筛选</Button>
      <Typography.Text type="secondary">
        筛选与统计作用于已加载的 {loadedCount} 台设备
        {hasMore ? "；可继续加载更多" : ""}
      </Typography.Text>
    </>
  );
}
