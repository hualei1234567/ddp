import { Drawer } from "antd";

import type { AuditLog } from "../../types/auditLog";
import AuditLogDetailContent from "./AuditLogDetailContent";

export interface AuditLogDetailDrawerProps {
  item: AuditLog | null;
  onClose: () => void;
  onFocusResource: (item: AuditLog) => void;
}

/**
 * 审计详情抽屉：只做外壳，内容见 ``AuditLogDetailContent``。
 * 关闭状态下不渲染内容，避免把上一条记录残留在 DOM 里。
 */
export default function AuditLogDetailDrawer({
  item,
  onClose,
  onFocusResource,
}: AuditLogDetailDrawerProps) {
  return (
    <Drawer
      title="审计详情"
      width={560}
      open={item !== null}
      onClose={onClose}
      destroyOnClose
    >
      {item ? (
        <AuditLogDetailContent item={item} onFocusResource={onFocusResource} />
      ) : null}
    </Drawer>
  );
}
