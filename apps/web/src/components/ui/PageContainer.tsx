import { Typography } from "antd";
import type { ReactNode } from "react";

interface PageContainerProps {
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
  filters?: ReactNode;
  children: ReactNode;
}

/**
 * 页面外壳：统一标题、说明、主操作区与筛选区。
 * 面包屑与项目上下文由顶层布局提供，避免每个页面重复实现。
 */
export default function PageContainer({
  title,
  description,
  extra,
  filters,
  children,
}: PageContainerProps) {
  return (
    <section className="ddp-page">
      <header className="ddp-page-header">
        <div className="ddp-page-heading">
          <Typography.Title level={3} className="ddp-page-title">
            {title}
          </Typography.Title>
          {description ? (
            <Typography.Paragraph
              type="secondary"
              className="ddp-page-description"
            >
              {description}
            </Typography.Paragraph>
          ) : null}
        </div>
        {extra ? <div className="ddp-page-extra">{extra}</div> : null}
      </header>
      {filters ? <div className="ddp-page-filters">{filters}</div> : null}
      <div className="ddp-page-body">{children}</div>
    </section>
  );
}
