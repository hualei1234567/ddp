import { Alert, Button, Card, Checkbox, Space, Tooltip, Typography } from "antd";

import {
  allSelfChecksPassed,
  SELF_CHECK_ITEMS,
  type CheckResults,
} from "../../../services/workbenchChecklist";

interface SelfCheckStepProps {
  results: CheckResults;
  onChange: (key: string, passed: boolean) => void;
  submitDisabledReason: string | null;
  onSubmit: () => void;
  submitting: boolean;
  online: boolean;
}

/** 工作台第 5 步：自检与提交。提交后等待服务端数据检查。 */
export default function SelfCheckStep({
  results,
  onChange,
  submitDisabledReason,
  onSubmit,
  submitting,
  online,
}: SelfCheckStepProps) {
  const ready = allSelfChecksPassed(SELF_CHECK_ITEMS, results);
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Alert
        type={ready ? "success" : "warning"}
        showIcon
        message={ready ? "自检项已完成" : "提交前请逐项完成自检"}
        description="提交后先进入“数据检查中”，不代表已验收；服务端会按状态机校验上传校验、自检与权限。"
      />
      {SELF_CHECK_ITEMS.map((item) => (
        <Card key={item.key} size="small">
          <Space direction="vertical" size={4} className="ddp-stack">
            <Checkbox
              checked={results[item.key] === "pass"}
              onChange={(event) => onChange(item.key, event.target.checked)}
            >
              {item.label}
            </Checkbox>
            <Typography.Text type="secondary">
              {item.description}
            </Typography.Text>
          </Space>
        </Card>
      ))}
      <Space direction="vertical" size={4} className="ddp-stack">
        <Tooltip title={submitDisabledReason ?? ""}>
          <span>
            <Button
              type="primary"
              size="large"
              className="ddp-touch-target"
              disabled={Boolean(submitDisabledReason)}
              loading={submitting}
              onClick={onSubmit}
            >
              {online ? "提交任务" : "待联网提交"}
            </Button>
          </span>
        </Tooltip>
        {submitDisabledReason ? (
          <Typography.Text type="secondary">
            {submitDisabledReason}
          </Typography.Text>
        ) : null}
      </Space>
    </Space>
  );
}
