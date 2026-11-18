import { Alert, Checkbox, Form, InputNumber, Radio, Space, Typography } from "antd";

import {
  REVIEW_MODE_VALUES,
  type ReviewMode,
} from "../../types/annotation";

const REVIEW_MODE_LABELS: Record<ReviewMode, string> = {
  SINGLE: "单人（SINGLE）",
  DOUBLE: "双标（DOUBLE）",
};

const REVIEW_MODE_OPTIONS = REVIEW_MODE_VALUES.map((value) => ({
  value,
  label: REVIEW_MODE_LABELS[value],
}));

export interface ReviewPolicySectionProps {
  /** 当前复核模式；由页面通过表单监听传入，便于逐状态测试。 */
  reviewMode: ReviewMode;
  /** 自动通过当前是否勾选。 */
  autoApprove: boolean;
  /** 权限提示：负责人或平台管理员才展示开关；服务端仍是权威。 */
  canAutoApprove: boolean;
  onAutoApproveChange: (checked: boolean) => void;
}

/**
 * 创建任务页的双标配置区（P2）。
 *
 * 只写既有 `iaa_policy` 契约字段：`review_mode` / `agreement_threshold` /
 * `auto_approve`。阈值按百分数录入，换算与校验在
 * `services/annotationTaskForm` 的纯函数里完成。
 */
export default function ReviewPolicySection({
  reviewMode,
  autoApprove,
  canAutoApprove,
  onAutoApproveChange,
}: ReviewPolicySectionProps) {
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Typography.Text strong>双标配置</Typography.Text>
      <Form.Item
        label="复核模式"
        name="review_mode"
        extra="SINGLE 为单人标注；DOUBLE 要求同一 Episode 由两名标注者覆盖，并按一致性阈值判定。创建后由服务端策略统一校验。"
      >
        <Radio.Group
          options={REVIEW_MODE_OPTIONS}
          optionType="button"
          buttonStyle="solid"
        />
      </Form.Item>
      {reviewMode === "DOUBLE" ? (
        <>
          <Form.Item
            label="一致性阈值（%）"
            name="agreement_threshold_percent"
            extra="0–100；留空使用服务端默认阈值。未达标提交会升级仲裁，不在此处自动通过。"
            rules={[
              {
                validator: async (_rule, value: number | null | undefined) => {
                  if (value === null || value === undefined) {
                    return;
                  }
                  if (!Number.isFinite(value) || value < 0 || value > 100) {
                    throw new Error("一致性阈值必须在 0 到 100 之间（百分数）");
                  }
                },
              },
            ]}
          >
            <InputNumber
              min={0}
              max={100}
              step={1}
              precision={1}
              style={{ width: 180 }}
              placeholder="留空使用服务端默认阈值"
            />
          </Form.Item>
          <Typography.Text type="secondary">
            判定指标由服务端默认策略决定（百分比一致率），本页不修改。
          </Typography.Text>
          {canAutoApprove ? (
            <Space direction="vertical" size={4} className="ddp-stack">
              <Checkbox
                checked={autoApprove}
                onChange={(event) => onAutoApproveChange(event.target.checked)}
              >
                达标后自动通过提交
              </Checkbox>
              {autoApprove ? (
                <Alert
                  type="warning"
                  showIcon
                  message="自动通过已开启"
                  description="一致性达标的提交将以项目负责人身份自动通过审批并写入审计；未达标仍会升级仲裁。取消勾选即关闭。"
                />
              ) : (
                <Typography.Text type="secondary">
                  默认关闭；只有项目负责人可以开启，勾选后还需要二次确认。
                </Typography.Text>
              )}
            </Space>
          ) : (
            <Typography.Text type="secondary">
              自动通过仅项目负责人可开启，当前账号不展示该开关。前端隐藏只是体验，服务端仍会校验权限。
            </Typography.Text>
          )}
        </>
      ) : null}
    </Space>
  );
}
