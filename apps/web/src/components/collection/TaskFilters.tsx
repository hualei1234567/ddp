import { Button, Input, Select, Typography } from "antd";
import { useEffect, useState } from "react";

import type { TaskListFilters } from "../../services/collectionUrl";

interface TaskFiltersProps {
  filters: TaskListFilters;
  statusOptions: { value: string; label: string }[];
  priorityOptions: { value: string; label: string }[];
  loadedCount: number;
  hasMore: boolean;
  disabled: boolean;
  onChange: (key: keyof TaskListFilters, value: string) => void;
  onReset: () => void;
  layout?: "inline" | "stacked";
}

/**
 * 采集任务筛选。
 * - status / assigned_to 交给服务端筛选；
 * - 关键词与优先级只作用于已加载数据，界面必须明确作用域。
 */
export default function TaskFilters({
  filters,
  statusOptions,
  priorityOptions,
  loadedCount,
  hasMore,
  disabled,
  onChange,
  onReset,
  layout = "inline",
}: TaskFiltersProps) {
  const [keyword, setKeyword] = useState(filters.keyword);
  const [assignedTo, setAssignedTo] = useState(filters.assignedTo);
  const stacked = layout === "stacked";

  useEffect(() => {
    setKeyword(filters.keyword);
  }, [filters.keyword]);

  useEffect(() => {
    setAssignedTo(filters.assignedTo);
  }, [filters.assignedTo]);

  return (
    <div className={stacked ? "ddp-filters ddp-filters-stacked" : "ddp-filters"}>
      <Input.Search
        allowClear
        value={keyword}
        placeholder="搜索已加载任务（编码或名称）"
        style={stacked ? { width: "100%" } : { width: 280, maxWidth: "100%" }}
        onChange={(event) => setKeyword(event.target.value)}
        onSearch={(value) => onChange("keyword", value.trim())}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="任务状态（服务端筛选）"
        style={stacked ? { width: "100%" } : { width: 190 }}
        value={filters.status || undefined}
        options={statusOptions}
        disabled={disabled}
        onChange={(value) => onChange("status", value ?? "")}
      />
      <Select
        allowClear
        placeholder="优先级（已加载范围）"
        style={stacked ? { width: "100%" } : { width: 150 }}
        value={filters.priority || undefined}
        options={priorityOptions}
        disabled={disabled}
        onChange={(value) => onChange("priority", value ?? "")}
      />
      <Input.Search
        allowClear
        value={assignedTo}
        placeholder="负责人用户 ID（服务端筛选）"
        style={stacked ? { width: "100%" } : { width: 260, maxWidth: "100%" }}
        onChange={(event) => setAssignedTo(event.target.value)}
        onSearch={(value) => onChange("assignedTo", value.trim())}
      />
      <Button block={stacked} onClick={onReset}>
        清空筛选
      </Button>
      <Typography.Text type="secondary" className="ddp-filter-scope">
        关键词与优先级作用于已加载的 {loadedCount} 条任务
        {hasMore ? "；状态与负责人由服务端筛选，可继续加载更多" : ""}
      </Typography.Text>
    </div>
  );
}
