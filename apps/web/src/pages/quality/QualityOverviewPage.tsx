import {
  AuditOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { Card, Col, List, Row, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import {
  buildAnnotationGoldSetListPath,
  buildAnnotationIAAListPath,
  buildAnnotationQualityListPath,
  buildQualityCheckPath,
} from "../../services/annotationUrl";

interface QualityEntry {
  key: string;
  title: string;
  icon: ReactNode;
  description: ReactNode;
  to: string;
  actionLabel: string;
}

/** 质量管理入口卡片：只说"去哪、要看什么前置"，不在这里拼数据。 */
function entries(projectId: string): QualityEntry[] {
  return [
    {
      key: "gold-sets",
      title: "金标准集（黄金集）",
      icon: <SafetyCertificateOutlined />,
      description:
        "登记带期望答案的黄金项并走审核 / 启用 / 退役流程。列表默认脱敏，期望答案需要在详情页显式请求，由服务端鉴权并写审计。",
      to: buildAnnotationGoldSetListPath(projectId),
      actionLabel: "进入金标准集",
    },
    {
      key: "quality-samples",
      title: "质检抽检",
      icon: <AuditOutlined />,
      description:
        "按抽样策略与风险分层查看抽检样本、复核人与闭环结论。结论未产生时显示 Pending，不会默认通过。",
      to: buildAnnotationQualityListPath(projectId),
      actionLabel: "进入质检抽检",
    },
    {
      key: "iaa-reports",
      title: "IAA 报告",
      icon: <LineChartOutlined />,
      description:
        "计算并查看标注者间一致性（一致率、Kappa / Alpha、Span F1、边界 MAE）。样本不足时状态为 INSUFFICIENT_DATA，指标显示 N/A 而不是 0。",
      to: buildAnnotationIAAListPath(projectId),
      actionLabel: "进入 IAA 报告",
    },
    {
      key: "quality-checks",
      title: "标注质量检查",
      icon: <TagsOutlined />,
      description:
        "提交时自动执行的规则检查，可在提交详情外单独查看历史并手动重跑。判定是三态：通过 / 未通过 / 无结论，无结论不等于通过。",
      to: buildQualityCheckPath(projectId),
      actionLabel: "进入质量检查",
    },
  ];
}

function QualityOverviewContent({ projectId }: { projectId: string }) {
  const items = entries(projectId);
  return (
    <PageContainer
      title="质量管理"
      description="标注质量能力总入口：金标准集、质检抽检、IAA 报告与提交质量检查。各页面的状态与结论全部以服务端记录为准，前端不做权限校验，也不根据状态推断可用动作。"
    >
      <Space direction="vertical" size={16} className="ddp-stack">
        <Row gutter={[16, 16]}>
          {items.map((entry) => (
            <Col key={entry.key} xs={24} md={12} xl={12}>
              <Card
                title={
                  <Space>
                    {entry.icon}
                    <Link to={entry.to}>{entry.title}</Link>
                  </Space>
                }
                extra={<Link to={entry.to}>{entry.actionLabel}</Link>}
              >
                <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  {entry.description}
                </Typography.Paragraph>
              </Card>
            </Col>
          ))}
        </Row>
        <Card title="口径与边界">
          <List
            size="small"
            dataSource={[
              "质量检查的整体结论只由 ERROR 级违规数决定：有任何 ERROR 即 FAILED，否则 PASSED；WARN 只提醒，不阻断。",
              "规则判定的「无结论」（passed = null）表示该规则对该记录没有判定依据，页面上单独计数，不得理解为通过。",
              "抽检与 IAA 按任务聚合、质量检查按提交聚合；缺少 URL 参数时页面会要求输入 ID，不会请求全量数据。",
              "质量门禁的放行与阻断由服务端裁决；前端只展示服务端返回的结论与计数。",
            ]}
            renderItem={(text) => (
              <List.Item>
                <Typography.Text type="secondary">{text}</Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}

export default function QualityOverviewPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="质量管理" description="质量数据必须按项目隔离展示。">
        <ProjectScopeRequired scope="质量管理" />
      </PageContainer>
    );
  }
  return <QualityOverviewContent key={projectId} projectId={projectId} />;
}
