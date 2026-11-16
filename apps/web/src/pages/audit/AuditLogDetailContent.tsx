import { Button, Descriptions, Space, Typography } from "antd";

import { formatActor } from "../../services/auditLogPresentation";
import { formatDateTime, formatIdentifier, formatText } from "../../services/format";
import type { AuditLog } from "../../types/auditLog";

export interface AuditLogDetailContentProps {
  item: AuditLog;
  /** 只看该资源：把 resource_type / resource_id 写回过滤条件。 */
  onFocusResource: (item: AuditLog) => void;
}

/**
 * 审计详情内容（与抽屉外壳分离，便于静态渲染测试）。
 *
 * 只读——没有删除或修改入口。`details` 是各业务用例写入的 JSON，按原样
 * 展示（pre 保留换行），前端不解释未知键，避免把领域字段讲错。
 *
 * 注意：``Descriptions`` 的直接子元素必须是 ``Descriptions.Item``，
 * 不能包一层自定义组件，否则 antd 收集不到内容（SSR 与 CSR 都一样）。
 */
export default function AuditLogDetailContent({
  item,
  onFocusResource,
}: AuditLogDetailContentProps) {
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="发生时间">
          {formatDateTime(item.occurred_at)}
        </Descriptions.Item>
        <Descriptions.Item label="结果">{formatText(item.result)}</Descriptions.Item>
        <Descriptions.Item label="资源类型">
          {formatText(item.resource_type)}
        </Descriptions.Item>
        <Descriptions.Item label="资源 ID">
          <Typography.Text code>{formatIdentifier(item.resource_id, 12, 8)}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="动作">{formatText(item.action)}</Descriptions.Item>
        <Descriptions.Item label="原状态">
          {formatText(item.from_status)}
        </Descriptions.Item>
        <Descriptions.Item label="新状态">
          {formatText(item.to_status)}
        </Descriptions.Item>
        <Descriptions.Item label="操作者">
          {formatActor(item.actor_role, item.actor_user_id)}
        </Descriptions.Item>
        <Descriptions.Item label="原因">{formatText(item.reason)}</Descriptions.Item>
        <Descriptions.Item label="request_id">
          {formatText(item.request_id)}
        </Descriptions.Item>
        <Descriptions.Item label="correlation_id">
          {formatText(item.correlation_id)}
        </Descriptions.Item>
        <Descriptions.Item label="审计记录 ID">
          <Typography.Text code>{formatIdentifier(item.audit_id, 12, 8)}</Typography.Text>
        </Descriptions.Item>
      </Descriptions>

      <div>
        <Typography.Text strong>明细（details）</Typography.Text>
        <pre className="ddp-audit-details">{JSON.stringify(item.details, null, 2)}</pre>
      </div>

      <Button onClick={() => onFocusResource(item)}>只看该资源</Button>
    </Space>
  );
}
