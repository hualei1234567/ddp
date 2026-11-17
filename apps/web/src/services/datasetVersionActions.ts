/**
 * 数据集版本动作的交互契约。
 *
 * 冻结是最容易被前端做错的一个动作：服务端要求动作体里的
 * `payload.qa_verified === true`（且执行人不能是构建人），只发 `reason`
 * 会直接 422。把「哪些动作带什么参数、要不要二次确认」集中在这里，
 * 页面只负责渲染，测试也只针对这一份规则。
 */

/** 冻结的确认文案；返回 null 表示该动作不需要额外确认。 */
export function datasetVersionActionConfirm(action: string): string | null {
  if (action === "freeze") {
    return "冻结后版本内容不可变，可被训练请求与导出引用。请确认你已独立复核该版本（QA 验证通过）。";
  }
  return null;
}

/** 动作级参数（服务端读作 command.payload）；null 表示不带参数。 */
export function datasetVersionActionPayload(
  action: string,
): Record<string, unknown> | null {
  if (action === "freeze") {
    // 用户在确认框里已经明确点了"已独立复核"，这里的 true 是对该确认的
    // 如实转述，不是前端替用户默认通过。
    return { qa_verified: true };
  }
  return null;
}
