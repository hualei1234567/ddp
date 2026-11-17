/**
 * 服务端 `allowed_actions` 的读取与附加（可选字段）。
 *
 * 背景：标注后端正在分批补齐 `allowed_actions`（任务 / 批次 / 分配 / 抽检 /
 * 黄金集已返回，Schema 与样本集尚未返回）。共享类型 `types/annotation.ts`
 * 不在本次改动范围，因此这里用可选的交叉类型承载该字段：
 * - 字段缺失时解析结果与原来完全一致；
 * - 页面通过 `resolveAnnotationActions(kind, status, item.allowed_actions)`
 *   优先使用服务端值，缺失时再按状态兜底。
 *
 * 待 `types/annotation.ts` 正式收录该字段后，可删除本模块并直接使用该字段。
 */
import { readOptionalStringArray } from "./validation";

export type WithAllowedActions<T> = T & { allowed_actions?: string[] };

/**
 * 把服务端 `allowed_actions` 附加到已解析对象上。
 *
 * 只接受字符串数组；结构不符时抛 `ContractValidationError`，不静默忽略。
 */
export function attachAllowedActions<T extends object>(
  item: T,
  record: Record<string, unknown>,
  path: string,
): WithAllowedActions<T> {
  const actions = readOptionalStringArray(
    record,
    "allowed_actions",
    `${path}.allowed_actions`,
  );
  if (actions === null) {
    return item;
  }
  return { ...item, allowed_actions: actions };
}
