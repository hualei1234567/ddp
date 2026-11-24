/**
 * 审核页的「QC 证据 → 创建返工」区（V51-010 / FR-025）。
 *
 * ## 红线：不新造审核流转
 *
 * 这里的"创建返工"**不是**新的状态机分支，而是：
 *
 * 1. 从该提交涉及 Episode 的**统一质量运行**里读未通过结论与证据定位；
 * 2. 把规则 id/版本、严重级别、字段/时间/流位置拼成**建议原因**；
 * 3. 交给父级调用**既有** `POST /annotation-submissions/{id}/actions/require-rework`
 *    ——权限、状态机、版本校验、职责分离全部仍由服务端裁决（蓝图 §5）。
 *
 * 因此本组件不判断"能不能返工"，只展示服务端会怎么判：`require-rework`
 * 只在 `REJECTED` / `RESOLVED_REJECTED` / `RESOLVED_REWORK` 三种状态可达
 * （见 `domain.annotation.state_machines`）。状态不符时按钮置灰并说明
 * 当前状态，避免用户点了才收到 409。
 *
 * ## 为什么按 Episode 聚合
 *
 * 统一质量运行的对象是 Episode（导入/数据集版本没有标注记录可返工）。一个
 * 提交可能覆盖多个 Episode，因此逐 Episode 拉最近一次成功运行，再取其中
 * 未通过结论。全部请求失败时如实报错，不把"没读到"渲染成"没有质量问题"。
 */

import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "../../services/api";
import {
  buildEvidenceJumpTarget,
  buildReworkContext,
  describeEvidenceLocatability,
  findingNeedsEvidence,
  REWORK_REACHABLE_SUBMISSION_STATUSES,
  type ReworkContext,
} from "../../services/qualityEvidence";
import { qualityRunApi } from "../../services/qualityRuns";
import {
  QUALITY_CATEGORY_LABELS,
  presentFindingStatus,
} from "../../services/qualityRunPresentation";
import type { AnnotationSubmission, ReviewIssue } from "../../types/annotation";
import type { QualityRun, QualityRunFinding } from "../../types/qualityRun";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

/** 复核问题时与 QC 严重级别的对应：ERROR→BLOCKER，WARN→MAJOR。 */
function issueSeverityFor(finding: QualityRunFinding): ReviewIssue["severity"] {
  return finding.severity === "ERROR" ? "BLOCKER" : "MAJOR";
}

export interface SubmissionQcEvidenceSectionProps {
  projectId: string;
  submission: AnnotationSubmission;
  /** 该提交涉及的 Episode（去重后由父级给出）。 */
  episodeIds: readonly string[];
  /** 执行既有 require-rework 动作；`issues` 附在复核问题里供追溯。 */
  onRequireRework: (input: {
    reason: string;
    issues: ReviewIssue[];
  }) => void;
  busy: boolean;
}

/** 单个 Episode 的质量结论；`run` 为空表示该 Episode 还没有成功运行。 */
interface EpisodeEvidence {
  episodeId: string;
  run: QualityRun | null;
  findings: QualityRunFinding[];
}

export default function SubmissionQcEvidenceSection({
  projectId,
  submission,
  episodeIds,
  onRequireRework,
  busy,
}: SubmissionQcEvidenceSectionProps) {
  const [data, setData] = useState<EpisodeEvidence[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(episodeIds.length > 0);
  const [reloadToken, setReloadToken] = useState(0);
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    if (episodeIds.length === 0) {
      setData([]);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const episodes = await Promise.all(
          episodeIds.map(async (episodeId) => {
            const page = await qualityRunApi.listRuns(projectId, {
              subject_type: "EPISODE",
              subject_id: episodeId,
              limit: 1,
              signal: controller.signal,
            });
            const run = page.items[0] ?? null;
            if (run === null) {
              return { episodeId, run: null, findings: [] } satisfies EpisodeEvidence;
            }
            const findings = await qualityRunApi.listFindings(
              projectId,
              run.run_id,
              { limit: 100, signal: controller.signal },
            );
            return {
              episodeId,
              run,
              findings: findings.items,
            } satisfies EpisodeEvidence;
          }),
        );
        if (controller.signal.aborted) {
          return;
        }
        setData(episodes);
      } catch (caught) {
        if (controller.signal.aborted) {
          return;
        }
        setData(null);
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError({
                kind: "network",
                message: "QC 证据加载失败。",
                cause: caught,
              }),
        );
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    })();
    return () => controller.abort();
  }, [episodeIds, projectId, reloadToken]);

  /** 全部未通过结论（跨 Episode），带 Episode 归属用于定位换算。 */
  const failing = useMemo(() => {
    if (data === null) {
      return [];
    }
    const items: {
      episodeId: string;
      runId: string;
      finding: QualityRunFinding;
    }[] = [];
    for (const item of data) {
      for (const finding of item.findings) {
        if (findingNeedsEvidence(finding)) {
          items.push({
            episodeId: item.episodeId,
            runId: item.run?.run_id ?? "",
            finding,
          });
        }
      }
    }
    return items;
  }, [data]);

  const selected =
    failing.find((item) => item.finding.finding_id === selectedFindingId) ??
    null;

  const canRequireRework = (
    REWORK_REACHABLE_SUBMISSION_STATUSES as readonly string[]
  ).includes(submission.status);

  const buildIssues = useCallback((): ReviewIssue[] => {
    // 每个未通过结论变成一条复核问题：code 用规则 id，message 附证据位置，
    // evidence_ref 指向运行+结论，让 ReviewIssue 可回溯到本次 QC。
    return failing.map(({ runId, finding }) => {
      const target = buildEvidenceJumpTarget(finding.evidence_location, null, null);
      return {
        code: finding.rule_id.slice(0, 100),
        severity: issueSeverityFor(finding),
        message: `${finding.message}（证据：${describeEvidenceLocatability(target)}）`.slice(
          0,
          2000,
        ),
        label_key: finding.evidence_location?.field_path?.startsWith("labels.")
          ? finding.evidence_location.field_path.slice("labels.".length)
          : null,
        item_ref: finding.finding_id,
        evidence_ref: runId === "" ? null : `quality-run:${runId}:${finding.finding_id}`,
      };
    });
  }, [failing]);

  const handleRequireRework = () => {
    if (selected === null) {
      return;
    }
    const context: ReworkContext = buildReworkContext(
      selected.finding,
      describeEvidenceLocatability(
        buildEvidenceJumpTarget(selected.finding.evidence_location, null, null),
      ),
    );
    // 原因里带全部未通过规则，避免"只返工一条、其余丢失"。
    const allRules = failing
      .map((item) => `${item.finding.rule_id}@${item.finding.rule_version}`)
      .join("、");
    const reason = `${context.reason}；本次共 ${failing.length} 条未通过结论：${allRules}`;
    onRequireRework({
      reason: reason.slice(0, 2000),
      issues: buildIssues(),
    });
  };

  let body: React.ReactNode;
  if (episodeIds.length === 0) {
    body = (
      <PageState
        compact
        variant="empty"
        title="该提交没有可定位 QC 证据的 Episode"
        description="统一质量运行以 Episode 为对象；没有 Episode 时无法给出时间轴证据。"
      />
    );
  } else if (loading) {
    body = (
      <PageState
        compact
        variant="loading"
        title="正在读取 QC 证据"
        description="读取各 Episode 最近一次质量运行与结论定位。"
      />
    );
  } else if (error !== null) {
    body = <ApiErrorState compact error={error} onRetry={() => setReloadToken((v) => v + 1)} />;
  } else if (data === null) {
    body = <PageState compact variant="empty" title="尚未读取 QC 证据" />;
  } else if (failing.length === 0) {
    body = (
      <Alert
        type="success"
        showIcon
        message="本次提交涉及的 Episode 没有未通过的质量结论"
        description="只统计 FAIL / ERROR 结论；无结论与未运行都不算通过，需另行判断。"
      />
    );
  } else {
    body = (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          共 {failing.length} 条未通过结论可附入返工原因；选择一条用于生成建议原因。
        </Typography.Text>
        {failing.map(({ episodeId, finding }) => {
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
              <Tag>{QUALITY_CATEGORY_LABELS[finding.category]}</Tag>
              <Typography.Text code>{finding.rule_id}</Typography.Text>
              <Typography.Text type="secondary">
                v{finding.rule_version}
              </Typography.Text>
              <Tag color={finding.severity === "ERROR" ? "red" : "orange"}>
                {finding.severity}
              </Tag>
              <Typography.Text type="secondary">
                Episode {episodeId.slice(0, 8)}…
              </Typography.Text>
              <ShowFindingStatus finding={finding} />
              <Button
                size="small"
                type={active ? "primary" : "default"}
                onClick={() =>
                  setSelectedFindingId(active ? null : finding.finding_id)
                }
              >
                {active ? "已选为返工依据" : "选为返工依据"}
              </Button>
            </Space>
          );
        })}
        <Space wrap size={8}>
          <Button
            type="primary"
            disabled={!canRequireRework || selected === null || busy}
            loading={busy}
            onClick={handleRequireRework}
          >
            创建返工（附带 QC 证据）
          </Button>
          {!canRequireRework ? (
            <Typography.Text type="secondary">
              当前提交状态为 {submission.status}，`require-rework` 仅在
              REJECTED / RESOLVED_REJECTED / RESOLVED_REWORK 可达；请先完成复核或仲裁。
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">
              返工仍走既有 require-rework：服务端校验版本、权限与职责分离。
            </Typography.Text>
          )}
        </Space>
      </Space>
    );
  }

  return (
    <Card size="small" title="QC 证据与返工依据（FR-025）">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="QC 提供证据与建议，不代替 Reviewer"
          description="是否返工仍由复核人按既有状态机决定；本区只把证据写进建议原因。"
        />
        {body}
      </Space>
    </Card>
  );
}

function ShowFindingStatus({ finding }: { finding: QualityRunFinding }) {
  const presentation = presentFindingStatus(finding.status);
  return <Tag color={presentation.tone === "error" ? "red" : "default"}>{presentation.label}</Tag>;
}
