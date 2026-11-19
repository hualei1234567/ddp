/**
 * 组件交互测试的 DOM 环境准备。
 *
 * 只有 `*.dom.test.tsx` 会在 jsdom 下运行（见 `vite.config.ts` 的
 * `environmentMatchGlobs`），其余测试仍是 node 环境。本文件因此必须
 * 在 node 环境下也能安全加载——`@testing-library/jest-dom/vitest`
 * 只注册匹配器，不读取 `document`。
 *
 * Vitest 未开启 `globals`，Testing Library 不会自动清理上一个用例
 * 渲染的 DOM；这里显式注册 `afterEach(cleanup)`，否则多个用例会共享
 * 同一份 document，`getByRole` 因重复元素失败。
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * antd 的 Modal 打开时会调用 `getComputedStyle(el, pseudoElt)` 计算滚动条宽度，
 * jsdom 未实现带伪元素的重载，会打印 "Not implemented" 噪声（不影响断言）。
 * 这里只补齐该重载：无伪元素时仍走 jsdom 原实现，避免掩盖真实样式问题。
 */
// 本文件作为全局 setupFiles，node 环境的测试也会加载它（那里没有 window）。
// 全部 DOM 修补必须在存在 `window` 时才执行，否则纯函数测试会被无关的环境
// 代码拖垮——这正是"默认 node、仅 *.dom.test 走 jsdom"的代价，必须显式处理。
if (typeof window !== "undefined" && typeof window.getComputedStyle === "function") {
  const nativeGetComputedStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = ((element: Element, pseudoElement?: string | null) =>
    pseudoElement
      ? nativeGetComputedStyle(element)
      : nativeGetComputedStyle(element)) as typeof window.getComputedStyle;
}

afterEach(() => {
  cleanup();
});
