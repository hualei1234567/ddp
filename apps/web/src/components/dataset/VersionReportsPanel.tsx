import { Alert, Space, Tag, Typography } from "antd";
import { useCallback } from "react";

import { useRequest } from "../../hooks/useRequest";
import { datasetApi } from "../../services/datasets";
import { presentLeakageStatus } from "../../services/datasetPresentation";
import type { LeakageReport, QualityReport } from "../../types/dataset";
import PageState from "../ui/PageState";
import CountsGrid from "./CountsGrid";
import StatusTag from "./StatusTag";

/**
 * 版本的质量与泄漏报告。报告由构建阶段生成、版本内不被修改；
 * 这里只负责读取与呈现，缺失时如实显示"未生成或加载失败"。
 */
export default function VersionReportsPanel({
  projectId,
  datasetId,
  versionId,
}: {
  projectId: string;
  datasetId: string;
  versionId: string;
}) {
  const loadQuality = useCallback(
    (signal: AbortSignal) =>
      datasetApi.getQualityReport(projectId, datasetId, versionId, { signal }),
    [projectId, datasetId, versionId],
  );
  const loadLeakage = useCallback(
    (signal: AbortSignal) =>
      datasetApi.getLeakageReport(projectId, datasetId, versionId, { signal }),
    [projectId, datasetId, versionId],
  );
  const quality = useRequest<QualityReport>(loadQuality);
  const leakage = useRequest<LeakageReport>(loadLeakage);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text strong>质量报告</Typography.Text>
        {quality.loading ? (
          <PageState compact variant="loading" description="加载中" />
        ) : null}
        {quality.error ? (
          <Typography.Text type="secondary">
            未生成或加载失败：{quality.error.message}
          </Typography.Text>
        ) : null}
        {quality.data ? (
          <>
            <CountsGrid
              items={[
                { label: "状态", value: quality.data.status },
                { label: "候选", value: quality.data.candidate_count },
                { label: "入选", value: quality.data.selected_count },
                { label: "排除", value: quality.data.excluded_count },
              ]}
            />
            {quality.data.blocking_issues.map((issue) => (
              <Alert key={issue} type="error" showIcon message={issue} />
            ))}
            <Typography.Text type="secondary">
              报告 Hash：{quality.data.report_sha256}
            </Typography.Text>
          </>
        ) : null}
      </Space>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text strong>泄漏报告</Typography.Text>
        {leakage.loading ? (
          <PageState compact variant="loading" description="加载中" />
        ) : null}
        {leakage.error ? (
          <Typography.Text type="secondary">
            未生成或加载失败：{leakage.error.message}
          </Typography.Text>
        ) : null}
        {leakage.data ? (
          <>
            <Space wrap>
              <StatusTag
                value={leakage.data.leakage_status}
                presentation={presentLeakageStatus}
              />
              <Tag>规则数：{leakage.data.checked_rule_count}</Tag>
              <Tag color={leakage.data.hard_violation_count > 0 ? "red" : "default"}>
                硬违规：{leakage.data.hard_violation_count}
              </Tag>
              <Tag color={leakage.data.soft_violation_count > 0 ? "orange" : "default"}>
                软违规：{leakage.data.soft_violation_count}
              </Tag>
            </Space>
            {leakage.data.violations.map((violation, index) => (
              <Alert
                key={`${violation.rule}:${index}`}
                type={violation.severity === "HARD" ? "error" : "warning"}
                showIcon
                message={`${violation.rule}（${violation.severity}）`}
                description={violation.detail ?? violation.item_id ?? undefined}
              />
            ))}
            <Typography.Text type="secondary">
              报告 Hash：{leakage.data.report_sha256}
            </Typography.Text>
          </>
        ) : null}
      </Space>
    </Space>
  );
}
