import { Button } from "antd";
import { useNavigate } from "react-router-dom";

import PageContainer from "../components/ui/PageContainer";
import PageState from "../components/ui/PageState";

export default function Forbidden() {
  const navigate = useNavigate();

  return (
    <PageContainer
      title="无权访问"
      description="前端不承担权威权限校验，页面是否可访问以服务端返回为准。"
    >
      <PageState
        variant="forbidden"
        action={
          <Button type="primary" onClick={() => navigate("/")}>
            返回工作台
          </Button>
        }
      />
    </PageContainer>
  );
}
