import { Button } from "antd";
import { useNavigate } from "react-router-dom";

import PageContainer from "../components/ui/PageContainer";
import PageState from "../components/ui/PageState";

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <PageContainer title="页面不存在">
      <PageState
        variant="empty"
        title="页面不存在"
        description="访问的地址可能已变更或被移除，请从导航重新进入。"
        action={
          <Button type="primary" onClick={() => navigate("/")}>
            返回工作台
          </Button>
        }
      />
    </PageContainer>
  );
}
