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
 * 不渲染任何业务数据，也不调用业务接口，只说明「这个模块还没做」。
 *
 * 文案刻意不说「即将上线」或「骨架已完成」：这两种说法都会让人以为功能
 * 已经存在只是没露出来。真实情况是后端能力也没有——诚实地说清楚，
 * 比给一个看起来很有希望的占位更有用。
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
            {item.label}的服务端能力尚未实现，因此这里没有数据可展示，也不提供
            任何操作。这是能力的真实状态，不是权限不足或加载失败。
            已实现的模块见左侧导航中不带「待开放」标记的条目。
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
