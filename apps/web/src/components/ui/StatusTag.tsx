import { Tag } from "antd";

/**
 * 通用状态标签：只展示服务端返回值，未识别值原样展示。
 *
 * 全站唯一的展示实现；各领域模块的 `StatusTag` 从这里再导出，
 * 避免每个阶段各写一份「value → Tag」。
 */
export interface StatusPresentationLike {
  label: string;
  color: string;
}

interface StatusTagProps {
  value: string | null | undefined;
  presentation: (value: string) => StatusPresentationLike;
  prefix?: string;
  /** 追加在标签文案后的计数等后缀（如「× 3」）。 */
  suffix?: string;
}

export default function StatusTag({
  value,
  presentation,
  prefix,
  suffix,
}: StatusTagProps) {
  if (!value) {
    return <Tag>未设置</Tag>;
  }
  const { label, color } = presentation(value);
  return (
    <Tag color={color}>
      {prefix ? `${prefix}：` : ""}
      {label}
      {suffix ? ` ${suffix}` : ""}
    </Tag>
  );
}
