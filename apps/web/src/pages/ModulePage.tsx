import { Button, Tag } from "antd";
import { useNavigate } from "react-router-dom";

import type { NavItem } from "../app/navigation";
import PageContainer from "../components/ui/PageContainer";
import PageState from "../components/ui/PageState";

interface ModulePageProps {
  item: NavItem;
}

/**
 * 未开放模块的统一占位页。
 * 不渲染任何业务数据，也不调用业务接口，只验证路由分区与布局。
 */
export default function ModulePage({ item }: ModulePageProps) {
  const navigate = useNavigate();

  return (
    <PageContainer
      title={item.label}
      description={item.description}
      extra={<Tag>待开放</Tag>}
    >
      <PageState
        variant="disabled"
        title={`${item.label}模块尚未开放`}
        description={
          <span>
            当前骨架已完成路由分区、响应式布局、统一 API client 与设计令牌。
            该模块的页面与接口将在对应阶段接入，当前不展示任何业务数据。
          </span>
        }
        action={
          <Button type="primary" onClick={() => navigate("/")}>
            返回工作台
          </Button>
        }
      />
    </PageContainer>
  );
}
