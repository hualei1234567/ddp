import { useMemo } from "react";

/**
 * 水印覆盖层：操作员 ID + 时间戳低透明度平铺。
 * pointer-events:none 不拦截交互；fixed 定位覆盖视口，仅标注页启用
 * （由调用方决定挂载）。文本用 CSS repeat 平铺，避免生成大量 DOM 节点。
 */
export default function WatermarkOverlay({
  operatorId,
  timestamp,
}: {
  operatorId: string;
  timestamp: string;
}) {
  const text = `${operatorId} · ${timestamp}`;
  // 每个 tile 的背景图由 SVG data URL 生成；text 进 URL 前做 URI 编码，
  // 防止操作员 ID 含 `#`/`"` 等字符破坏 data URL 结构。
  const backgroundImage = useMemo(() => {
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">` +
      `<text x="16" y="90" fill="rgba(27,36,48,0.06)" font-size="14" ` +
      `font-family="sans-serif" transform="rotate(-20 160 90)">${text}</text>` +
      `</svg>`;
    // 整体 URI 编码后内嵌 data URL：操作员 ID 与时间戳都可能含
    // `<`/`"`/`#` 等会破坏 CSS url() 或 SVG 结构的字符。
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  }, [text]);

  return (
    <div
      aria-hidden
      data-testid="watermark-overlay"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1,
        pointerEvents: "none",
        backgroundImage,
        backgroundRepeat: "repeat",
      }}
    />
  );
}
