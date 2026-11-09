import { Descriptions } from "antd";

export interface CountItem {
  label: string;
  value: number | string | null | undefined;
}

export default function CountsGrid({
  items,
  column = 4,
}: {
  items: CountItem[];
  column?: number;
}) {
  return (
    <Descriptions
      size="small"
      column={column}
      items={items.map((item) => ({
        key: item.label,
        label: item.label,
        children: item.value ?? "—",
      }))}
    />
  );
}
