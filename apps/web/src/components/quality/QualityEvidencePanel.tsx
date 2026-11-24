/**
 * QC 证据定位面板（V51-010 / FR-025）。
 *
 * ## 只做"把规则给出的证据显示出来"
 *
 * 面板**不重新判定**质量：`severity` / `status` / `score` 全部原样来自服务端。
 * 它只回答三个界面问题：
 *
 * 1. 这条结论指向哪个时间区间、哪一路流、哪个字段（来自 `evidence_location`）；
 * 2. 怎么在**既有播放器与时间轴**上跳到那个位置（不新建播放器，蓝图 §1）；
 * 3. 需要返工时，怎么把 issue context 附在既有 `require-rework` 动作上
 *    （不新造审核流转，蓝图 §5）。
 *
 * ## 红线：不代替 Reviewer
 *
 * 这里的"创建返工"只是**填充表单 + 打开既有确认入口**。真正的状态迁移仍走
 * `POST /annotation-submissions/{id}/actions/require-rework`：权限、状态机、
 * 版本校验、职责分离全部由服务端裁决。面板不会因为"有 ERROR 级 QC 结论"
 * 就自动把提交推进到 `REWORK_REQUIRED`——那正是蓝图 §5 明令禁止的。
 */

import { AimOutlined, ExportOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  buildEvidenceJumpTarget,
  buildReworkContext,
  describeEvidenceLocatability,
  findingNeedsEvidence,
  formatRelativeSeconds,
  type ReworkContext,
} from "../../services/qualityEvidence";
import {
  QUALITY_CATEGORY_LABELS,
  formatQualityScore,
  presentFindingStatus,
  presentSeverity,
} from "../../services/qualityRunPresentation";
import type {
  QualityRun,
  QualityRunFinding,
} from "../../types/qualityRun";
import QualityToneTag from "./QualityToneTag";

export type { ReworkContext } from "../../services/qualityEvidence";

export interface QualityEvidencePanelProps {
  run: QualityRun;
  findings: readonly QualityRunFinding[];
  /** Episode 绝对起止纳秒；用于把证据区间换算成播放器相对秒。 */
  episodeStartNs: string | number | null;
  episodeEndNs: string | number | null;
  /** 当前选中结论；受控，由父级持有以便与播放器联动。 */
  selectedFindingId: string | null;
  onSelectFinding: (findingId: string | null) => void;
  /**
   * 创建返工：父级负责走**既有** require-rework 入口。
   * 未提供时按钮置灰并说明"当前上下文不提供返工入口"，而不是静默消失。
   */
  onCreateRework?: (context: ReworkContext) => void;
  /** 返工入口不可用的原因（例如提交不在可返工状态、无权限）。 */
  reworkUnavailableReason?: string | null;
  /** 返工请求进行中。 */
  reworkBusy?: boolean;
}

function evidenceValueText(finding: QualityRunFinding): string {
  // 阈值/期望值/实际值目前存放在自由 evidence 里。只按已知键读取，
  // 读不到就显示 "—"：不猜结构，也不把整块 JSON 塞进表格。
  const evidence = finding.evidence;
  if (evidence === null) {
    return "—";
  }
  const actual = evidence["actual"];
  const expected = evidence["expected"] ?? evidence["threshold"];
  const parts: string[] = [];
  if (expected !== undefined) {
    parts.push(`期望 ${formatEvidenceScalar(expected)}`);
  }
  if (actual !== undefined) {
    parts.push(`实际 ${formatEvidenceScalar(actual)}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "—";
}

function formatEvidenceScalar(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value === null || value === undefined) {
    return "—";
  }
  // 复合结构不在这里展开：表格单元放整块 JSON 会挤掉其它列。
  return "（见证据详情）";
}

/**
 * 单条结论的证据卡片：规则/版本/严重级别/字段/期望-实际/阈值 + 跳转说明。
 *
 * 时间轴与播放器由父级持有（Episode 详情已渲染它们），本组件只**发出**选择
 * 信号，避免出现第二个播放器实例（蓝图 §1）。
 */
export default function QualityEvidencePanel({
  run,
  findings,
  episodeStartNs,
  episodeEndNs,
  selectedFindingId,
  onSelectFinding,
  onCreateRework,
  reworkUnavailableReason = null,
  reworkBusy = false,
}: QualityEvidencePanelProps) {
  const actionable = useMemo(
    () => findings.filter((finding) => findingNeedsEvidence(finding)),
    [findings],
  );
  const [localError, setLocalError] = useState<string | null>(null);

  const selected = useMemo(
    () =>
      actionable.find((finding) => finding.finding_id === selectedFindingId) ??
      null,
    [actionable, selectedFindingId],
  );

  const target = useMemo(
    () =>
      selected === null
        ? null
        : buildEvidenceJumpTarget(
            selected.evidence_location,
            episodeStartNs,
            episodeEndNs,
          ),
    [episodeEndNs, episodeStartNs, selected],
  );

  const handleCreateRework = () => {
    setLocalError(null);
    if (selected === null || target === null) {
      return;
    }
    if (onCreateRework === undefined) {
      setLocalError("当前上下文没有提供返工入口。");
      return;
    }
    onCreateRework(
      buildReworkContext(selected, describeEvidenceLocatability(target)),
    );
  };

  return (
    <Card
      size="small"
      title="QC 证据定位"
      extra={
        <Typography.Text type="secondary">
          规则集 {run.rule_set_version.slice(0, 19)}…
        </Typography.Text>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="QC 提供证据与建议，不代替 Reviewer"
          description="是否返工、是否通过仍由复核人按既有状态机与权限决定；此处只预填建议原因，不自动改变提交状态。"
        />

        {actionable.length === 0 ? (
          <Alert
            type="success"
            showIcon
            message="本次运行没有需要定位的未通过结论"
            description="只有 FAIL / ERROR 结论才附带证据定位；通过结论与无结论不计入此处。"
          />
        ) : (
          <Space direction="vertical" size={6} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              {actionable.length} 条未通过结论可定位；点击选择后在下方时间轴与
              播放器查看证据。
            </Typography.Text>
            {actionable.map((finding) => {
              const jump = buildEvidenceJumpTarget(
                finding.evidence_location,
                episodeStartNs,
                episodeEndNs,
              );
              const active = finding.finding_id === selectedFindingId;
              return (
                <Space
                  key={finding.finding_id}
                  wrap
                  size={8}
                  style={{
                    width: "100%",
                    padding: "4px 8px",
                    borderRadius: 4,
                    background: active ? "rgba(22,119,255,0.08)" : undefined,
                  }}
                >
                  <Tag color={active ? "blue" : undefined}>
                    {QUALITY_CATEGORY_LABELS[finding.category]}
                  </Tag>
                  <Typography.Text code>{finding.rule_id}</Typography.Text>
                  <Typography.Text type="secondary">
                    v{finding.rule_version}
                  </Typography.Text>
                  <QualityToneTag presentation={presentSeverity(finding.severity)} />
                  <QualityToneTag
                    presentation={presentFindingStatus(finding.status)}
                  />
                  <Typography.Text type="secondary">
                    {describeEvidenceLocatability(jump)}
                  </Typography.Text>
                  <Button
                    size="small"
                    icon={<AimOutlined />}
                    type={active ? "primary" : "default"}
                    aria-pressed={active}
                    onClick={() =>
                      onSelectFinding(active ? null : finding.finding_id)
                    }
                  >
                    {active ? "已定位" : "定位证据"}
                  </Button>
                </Space>
              );
            })}
          </Space>
        )}

        {selected !== null && target !== null ? (
          <Card size="small" type="inner" title="证据详情">
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2 }}
              items={[
                {
                  key: "rule",
                  label: "规则 / 版本",
                  children: (
                    <Space direction="vertical" size={0}>
                      <Typography.Text code>{selected.rule_id}</Typography.Text>
                      <Typography.Text type="secondary">
                        {selected.rule_version}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  key: "severity",
                  label: "严重级别",
                  children: (
                    <QualityToneTag
                      presentation={presentSeverity(selected.severity)}
                    />
                  ),
                },
                {
                  key: "category",
                  label: "类别",
                  children: QUALITY_CATEGORY_LABELS[selected.category],
                },
                {
                  key: "score",
                  label: "评分",
                  children: formatQualityScore(selected.score),
                },
                {
                  key: "field_path",
                  label: "字段路径",
                  children: selected.evidence_location?.field_path ?? "—",
                },
                {
                  key: "stream",
                  label: "媒体流",
                  children: selected.evidence_location?.stream_id ?? "—",
                },
                {
                  key: "time_range",
                  label: "证据区间（相对 Episode）",
                  children:
                    target.highlight === null
                      ? "—"
                      : `${formatRelativeSeconds(target.highlight.startSeconds)} – ${formatRelativeSeconds(target.highlight.endSeconds)}`,
                },
                {
                  key: "expected_actual",
                  label: "期望 / 实际",
                  children: evidenceValueText(selected),
                },
                {
                  key: "message",
                  label: "结论说明",
                  children: selected.message,
                },
              ]}
            />
            {target.locatable ? (
              <Typography.Text type="secondary">
                {target.seekSeconds === null
                  ? "该结论没有时间维度：播放器不跳转，时间轴也不高亮。"
                  : `已请求播放器跳转到 ${formatRelativeSeconds(target.seekSeconds)}；时间轴高亮对应区间。`}
              </Typography.Text>
            ) : (
              <Alert
                type="warning"
                showIcon
                message="该结论无法定位"
                description="规则没有给出时间区间、流或字段，界面不猜测位置。"
              />
            )}
            <Space wrap size={8} style={{ marginTop: 8 }}>
              <Button
                type="primary"
                icon={<ExportOutlined />}
                disabled={
                  onCreateRework === undefined ||
                  reworkUnavailableReason !== null ||
                  reworkBusy
                }
                loading={reworkBusy}
                onClick={handleCreateRework}
              >
                用该证据创建返工
              </Button>
              {reworkUnavailableReason !== null ? (
                <Typography.Text type="secondary">
                  {reworkUnavailableReason}
                </Typography.Text>
              ) : null}
            </Space>
            {localError !== null ? (
              <Alert type="error" showIcon message={localError} />
            ) : null}
            <Typography.Text type="secondary">
              返工仍走既有 require-rework 动作：服务端校验状态机、版本与职责分离；
              本按钮不推进任何状态。
            </Typography.Text>
          </Card>
        ) : null}

        <Link to="#" onClick={(event) => event.preventDefault()}>
          <Typography.Text type="secondary">
            证据来源：本次运行的结论明细（只追加记录，重跑不会覆盖）
          </Typography.Text>
        </Link>
      </Space>
    </Card>
  );
}
