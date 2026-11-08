import { Alert, Button, Card, Radio, Space, Tag, Typography } from "antd";

import { summarizeChecklist } from "../../services/workbenchChecklist";
import type {
  CheckResult,
  CheckResults,
  WorkbenchCheckItem,
} from "../../services/workbenchChecklist";

interface WorkbenchChecklistProps {
  items: WorkbenchCheckItem[];
  results: CheckResults;
  disabled?: boolean;
  onChange: (key: string, result: CheckResult) => void;
  onRecheck: () => void;
}

const RESULT_OPTIONS: { value: CheckResult; label: string }[] = [
  { value: "pass", label: "通过" },
  { value: "warn", label: "警告" },
  { value: "blocked", label: "阻塞" },
];

const RESULT_COLOR: Record<CheckResult, string> = {
  pending: "default",
  pass: "green",
  warn: "orange",
  blocked: "red",
};

const RESULT_LABEL: Record<CheckResult, string> = {
  pending: "待确认",
  pass: "通过",
  warn: "警告",
  blocked: "阻塞",
};

/**
 * 开工检查。所有阻塞项必须显式选择“通过”才允许发起开始动作；
 * 警告与阻塞分开标识。结果仅本地保存，服务端仍会独立校验。
 */
export default function WorkbenchChecklist({
  items,
  results,
  disabled = false,
  onChange,
  onRecheck,
}: WorkbenchChecklistProps) {
  const summary = summarizeChecklist(items, results);

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Alert
        type={summary.ready ? "success" : "warning"}
        showIcon
        message={
          summary.ready
            ? "所有阻塞项已通过，可发起开始采集"
            : "存在未通过或未确认的阻塞项，开始采集保持禁用"
        }
        description={
          summary.ready
            ? "检查结果仅保存在当前页面；服务端仍会校验设备、标定与权限。"
            : `待确认 ${summary.pending} 项、警告 ${summary.warn} 项、阻塞 ${summary.blocked} 项。未通过项：${summary.blockingRemaining.join("、")}。`
        }
      />
      {items.map((item) => {
        const result = results[item.key] ?? "pending";
        return (
          <Card
            key={item.key}
            size="small"
            title={
              <Space wrap>
                <span>{item.label}</span>
                <Tag color={RESULT_COLOR[result]}>{RESULT_LABEL[result]}</Tag>
                {item.blocking ? <Tag color="red">阻塞项</Tag> : <Tag>非阻塞</Tag>}
              </Space>
            }
          >
            <Space direction="vertical" size={8} className="ddp-stack">
              <Typography.Text type="secondary">
                {item.description}
              </Typography.Text>
              <Radio.Group
                value={result === "pending" ? undefined : result}
                disabled={disabled}
                onChange={(event) =>
                  onChange(item.key, event.target.value as CheckResult)
                }
              >
                {RESULT_OPTIONS.map((option) => (
                  <Radio key={option.value} value={option.value}>
                    {option.label}
                  </Radio>
                ))}
              </Radio.Group>
            </Space>
          </Card>
        );
      })}
      <Button onClick={onRecheck} disabled={disabled}>
        重新检测并清空本地结果
      </Button>
    </Space>
  );
}
