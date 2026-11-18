/** 视频审看的纯逻辑：倍速、时间轴与同屏机位选择。
 *
 * 与页面组件分开，便于单测在 node 环境下直接验证边界行为；页面只负责渲染。
 */

/** 审看倍速档位；与产品要求一致（0.5/1/1.5/2）。 */
export const PLAYBACK_RATES = [0.5, 1, 1.5, 2] as const;

/** 同屏最多机位数量：超过两路并排会挤到无法审看。 */
export const MAX_SIDE_BY_SIDE = 2;

/** 秒 -> mm:ss；负值/非有限值按 0 处理。 */
export function formatClock(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const total = Math.floor(safe);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

/** 把任意跳转目标夹到 [0, duration]；duration 未知（0）时回到 0。 */
export function clampSeek(value: number, duration: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  const upper = Number.isFinite(duration) && duration > 0 ? duration : 0;
  return Math.min(Math.max(value, 0), upper);
}

/** 过滤掉不属于当前片段的机位，并保留选择顺序、最多两路。 */
export function normalizeCameraSelection(
  selected: readonly string[],
  cameras: readonly string[],
): string[] {
  const available = new Set(cameras);
  return selected
    .filter((camera) => available.has(camera))
    .slice(0, MAX_SIDE_BY_SIDE);
}

/** 首次进入某片段时同屏展示前两路机位。 */
export function defaultCameraSelection(cameras: readonly string[]): string[] {
  return cameras.slice(0, MAX_SIDE_BY_SIDE);
}

/**
 * 勾选/取消同屏机位。
 *
 * 已选满两路时再勾第三路，替换**最早**勾选的那一路（保留最新选择），
 * 避免用户点了却没反应。
 */
export function toggleCameraSelection(
  selected: readonly string[],
  camera: string,
  checked: boolean,
  cameras: readonly string[],
): string[] {
  const available = new Set(cameras);
  const base = normalizeCameraSelection(selected, cameras);
  if (!available.has(camera)) {
    return base;
  }
  if (!checked) {
    return base.filter((item) => item !== camera);
  }
  if (base.includes(camera)) {
    return base;
  }
  return [...base, camera].slice(-MAX_SIDE_BY_SIDE);
}
