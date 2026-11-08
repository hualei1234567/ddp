/**
 * 现场工作台的本地检查清单。
 *
 * 清单项来自 PRD §7.4 / UX §6.6 的开工检查类别，不是服务端业务状态。
 * 勾选结果只保存在当前页面，用于阻止“未核对就发起开始动作”；
 * 真正的开工门禁、设备状态与标定有效性仍由服务端 start 动作校验。
 */

export const DEFAULT_WORKBENCH_STEP = "confirm" as const;

export type CheckResult = "pending" | "pass" | "warn" | "blocked";

export interface WorkbenchCheckItem {
  key: string;
  label: string;
  description: string;
  /** true 表示该项未通过时不得发起开始采集。 */
  blocking: boolean;
}

export const WORKBENCH_CHECK_ITEMS: WorkbenchCheckItem[] = [
  {
    key: "devices",
    label: "设备与传感器",
    description: "核对任务分配的机器人、设备与传感器均在场且可用。",
    blocking: true,
  },
  {
    key: "calibration",
    label: "标定有效性",
    description: "确认任务所需标定在本次采集时段内有效。",
    blocking: true,
  },
  {
    key: "time_sync",
    label: "时间同步",
    description: "确认设备与主机时间源一致，时间同步状态可解释。",
    blocking: true,
  },
  {
    key: "storage",
    label: "存储与电量",
    description: "确认采集设备存储空间与电量满足本批次需要。",
    blocking: true,
  },
  {
    key: "scene",
    label: "场景与安全",
    description: "按 SOP 完成场景布置，并确认现场安全要求已落实。",
    blocking: true,
  },
  {
    key: "sop",
    label: "SOP 版本确认",
    description: "确认现场查看的 SOP 版本与任务记录一致。",
    blocking: true,
  },
];

export interface SelfCheckItem {
  key: string;
  label: string;
  description: string;
}

export const SELF_CHECK_ITEMS: SelfCheckItem[] = [
  {
    key: "uploads_verified",
    label: "上传与服务端校验",
    description: "本次提交的文件已上传完成，且服务端校验状态已确认。",
  },
  {
    key: "anomalies",
    label: "异常记录",
    description: "现场异常已记录并分级，或确认本次无异常。",
  },
  {
    key: "sop_followed",
    label: "SOP 执行",
    description: "已按任务记录中的 SOP 版本完成采集步骤。",
  },
  {
    key: "rework",
    label: "返工项",
    description: "如为返工任务，返工要求中的问题项已全部处理。",
  },
];

export type CheckResults = Record<string, CheckResult>;

export function createEmptyCheckResults(
  items: { key: string }[],
): CheckResults {
  const results: CheckResults = {};
  for (const item of items) {
    results[item.key] = "pending";
  }
  return results;
}

export interface ChecklistSummary {
  pending: number;
  pass: number;
  warn: number;
  blocked: number;
  /** 所有阻塞项均通过（warn 不视为通过，需显式确认）。 */
  ready: boolean;
  blockingRemaining: string[];
}

export function summarizeChecklist(
  items: WorkbenchCheckItem[],
  results: CheckResults,
): ChecklistSummary {
  let pending = 0;
  let pass = 0;
  let warn = 0;
  let blocked = 0;
  const blockingRemaining: string[] = [];

  for (const item of items) {
    const result = results[item.key] ?? "pending";
    if (result === "pending") {
      pending += 1;
    } else if (result === "pass") {
      pass += 1;
    } else if (result === "warn") {
      warn += 1;
    } else {
      blocked += 1;
    }
    if (item.blocking && result !== "pass") {
      blockingRemaining.push(item.label);
    }
  }

  return {
    pending,
    pass,
    warn,
    blocked,
    ready: blockingRemaining.length === 0,
    blockingRemaining,
  };
}

export function allSelfChecksPassed(
  items: SelfCheckItem[],
  results: CheckResults,
): boolean {
  return items.every((item) => results[item.key] === "pass");
}

const UPLOAD_ALLOWED_STATUSES = new Set([
  "IN_PROGRESS",
  "PAUSED",
  "BLOCKED",
  "REWORK",
]);

/**
 * 前端上传入口的可用性提示。服务端仍会校验任务状态与权限；
 * 该函数只用于按钮禁用与原因说明，不作为权限判定。
 */
export function canUploadForTaskStatus(status: string): boolean {
  return UPLOAD_ALLOWED_STATUSES.has(status);
}
