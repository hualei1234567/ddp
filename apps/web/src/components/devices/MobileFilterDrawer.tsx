import { FilterOutlined } from "@ant-design/icons";
import { Button, Drawer, Space } from "antd";
import { useState } from "react";

import DeviceFilters from "./DeviceFilters";

interface MobileFilterDrawerProps {
  keyword: string;
  statusFilter: string;
  typeFilter: string;
  statusOptions: { value: string; label: string }[];
  typeOptions: { value: string; label: string }[];
  loadedCount: number;
  hasMore: boolean;
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onReset: () => void;
  /** 已启用的筛选条件数量，用于窄屏入口提示。 */
  activeCount: number;
}

/**
 * 窄屏筛选入口：筛选区不再横向挤占内容，改为全屏抽屉；
 * 抽屉内控件纵向全宽，符合现场端单手触控。
 */
export default function MobileFilterDrawer({
  activeCount,
  onReset,
  ...filterProps
}: MobileFilterDrawerProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="ddp-mobile-filters">
      <Space wrap>
        <Button
          icon={<FilterOutlined />}
          className="ddp-touch-target"
          onClick={() => setOpen(true)}
        >
          筛选{activeCount > 0 ? `（${activeCount}）` : ""}
        </Button>
        {activeCount > 0 ? (
          <Button type="link" onClick={onReset}>
            清空筛选
          </Button>
        ) : null}
      </Space>
      <Drawer
        open={open}
        placement="bottom"
        height="100%"
        title="筛选设备"
        className="ddp-filter-drawer"
        onClose={() => setOpen(false)}
        footer={
          <Button
            type="primary"
            block
            size="large"
            className="ddp-touch-target"
            onClick={() => setOpen(false)}
          >
            完成
          </Button>
        }
      >
        <DeviceFilters
          {...filterProps}
          layout="stacked"
          onReset={onReset}
          onSearchSubmit={() => setOpen(false)}
        />
      </Drawer>
    </div>
  );
}
