/**
 * 导出格式的前端展示与「格式 ↔ 版本」配对规则。
 *
 * 领域层（`domain/datasets/export_validation.py`）已经把格式与版本绑成一对：
 * `LE_ROBOT` 只能配 `ddp.lerobot/1.0.0`，`CANONICAL` 只能配
 * `ddp.canonical/1.0.0`。前端不能只给一个自由输入的版本框——那样用户能选出
 * 交叉组合，提交后才被服务端以 `export_format_version_unsupported` 拒绝。
 *
 * 取值集合是可增长的（契约用 `x-extensible-enum` 声明）。因此展示层必须容忍
 * 未知格式：回退成原样回显，而不是当作某个已知格式渲染。
 */

export const EXPORT_FORMATS = ["LE_ROBOT", "CANONICAL"] as const;

export type ExportFormat = (typeof EXPORT_FORMATS)[number];

/** 格式 ↔ 版本。新增格式必须同时在这里与领域层登记。 */
const FORMAT_VERSIONS: Readonly<Record<ExportFormat, string>> = {
  LE_ROBOT: "ddp.lerobot/1.0.0",
  CANONICAL: "ddp.canonical/1.0.0",
};

/** 每个格式的一句话说明，用于表单提示与列表展示。 */
const FORMAT_HINTS: Readonly<Record<ExportFormat, string>> = {
  LE_ROBOT: "LeRobot 元数据 profile：帧级映射未实现，尚未通过真实 Loader 验证。",
  CANONICAL: "平台规范格式（Canonical JSON + NDJSON），不能直接喂给 LeRobot Loader。",
};

export function isKnownExportFormat(value: string): value is ExportFormat {
  return (EXPORT_FORMATS as readonly string[]).includes(value);
}

/** 已知格式返回配对版本；未知格式返回 `null`（调用方不得猜）。 */
export function defaultFormatVersion(format: string): string | null {
  return isKnownExportFormat(format) ? FORMAT_VERSIONS[format] : null;
}

export function describeExportFormat(format: string): string {
  return isKnownExportFormat(format) ? FORMAT_HINTS[format] : `未知导出格式：${format}`;
}

/** 表单下拉选项。除已知格式外，已存在任务的未知格式需要能回显。 */
export function exportFormatOptions(
  current: string | undefined,
): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = EXPORT_FORMATS.map((format) => ({
    value: format,
    label: format,
  }));
  if (current !== undefined && current !== "" && !isKnownExportFormat(current)) {
    options.push({ value: current, label: `${current}（未知格式）` });
  }
  return options;
}
