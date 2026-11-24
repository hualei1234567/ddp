/**
 * 标注工作量/容量的展示格式化（唯一实现）。
 *
 * 为什么单独成文件而不是放在组件里：`react-refresh/only-export-components`
 * 不允许组件文件导出纯函数；更重要的是，工时与占比的格式化在「周期工作量」
 * 与「分配前容量预览」两处都要用，各写一份必然出现"90 分钟"和"1.5 小时"
 * 两种口径。
 *
 * 语义红线：`null` 表示部署**未配置**，不渲染成 0。0 是真实的零容量，
 * 与"没配"是两件事——把两者混同会让运营者把所有人看成已超载。
 */

/** 分钟 → 人类可读时长；0 也如实渲染成「0 分钟」，不是空白。 */
export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) {
    return `${rest} 分钟`;
  }
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分钟`;
}

/** 占比 → 百分比；`null`（未配置上限）显式说明，不显示 0%。
 *
 * 「未配置」的措辞由调用方给：周期工作量说的是「不计算利用率」，容量预览说的
 * 是「不计算占比」——两者统计对象不同，强行共用一句话会让其中一页读起来像
 * 另一个概念的字段。共用的是百分比算法，不是业务措辞。
 */
export function formatRatio(
  ratio: number | null,
  unconfiguredText: string,
): string {
  if (ratio === null) {
    return unconfiguredText;
  }
  return `${(ratio * 100).toFixed(1)}%`;
}
