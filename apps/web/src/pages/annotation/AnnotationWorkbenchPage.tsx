import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AnnotationConflictModal from "../../components/annotation/AnnotationConflictModal";
import OverdueSummaryCard from "../../components/annotation/OverdueSummaryCard";
import WorkbenchBody from "../../components/annotation/WorkbenchBody";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useAnnotationDraft } from "../../hooks/useAnnotationDraft";
import { useAnnotationWorkbench } from "../../hooks/useAnnotationWorkbench";
import { useWorkbenchConflictFlow } from "../../hooks/useWorkbenchConflictFlow";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useProjectId } from "../../hooks/useProjectId";
import { annotationApi } from "../../services/annotation";
import {
  annotationSuccess,
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../../services/annotationPresentation";
import {
  buildAnnotationTaskDetailPath,
  buildAnnotationTaskListPath,
} from "../../services/annotationUrl";
import {
  buildWorkbenchRecords,
  hasHiddenRequiredLabels,
  schemaSnapshotMatches,
} from "../../services/annotationWorkbench";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DataSource } from "../../types/annotation";

const EDITABLE_TASK_STATUSES = new Set(["IN_PROGRESS", "REWORK"]);
const EDITABLE_BATCH_STATUSES = new Set(["IN_PROGRESS", "REWORK"]);

function WorkbenchContent({
  projectId,
  assignmentId,
}: {
  projectId: string;
  assignmentId: string;
}) {
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const workbench = useAnnotationWorkbench(projectId, assignmentId);
  const data = workbench.data?.data ?? null;
  const partialErrors = workbench.data?.partialErrors ?? [];
  const [dataSource, setDataSource] = useState<DataSource>("UNKNOWN");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);
  const [validationAttempted, setValidationAttempted] = useState(false);

  const draft = useAnnotationDraft({
    projectId,
    assignmentId,
    schema: data?.schema ?? null,
    episodes: data?.episodes ?? [],
  });

  // FR-019：冲突恢复编排收在 `useWorkbenchConflictFlow`（页面只做渲染）。
  const conflict = useWorkbenchConflictFlow({
    projectId,
    assignmentId,
    taskId: data?.task.task_id ?? "",
    taskCode: data?.task.code ?? "",
    assignmentIdForCreate: data?.assignment.assignment_id ?? "",
    dataSource,
    schema: data?.schema ?? null,
    episodes: data?.episodes ?? [],
    readValues: () => draft.values,
    applyValues: draft.replaceValues,
    latestSubmission: data?.latestSubmission ?? null,
    setFeedback,
    reload: workbench.reload,
    resetDraft: draft.resetDraft,
    setBusyAction,
  });
  const recovery = conflict.recovery;

  // FR-019「不丢数据」：刷新后若存在未合并冲突快照，恢复冲突当时的输入。
  // 只在 ready 后跑一次；没有快照时完全不影响既有行为。
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    if (restored || !draft.ready || data === null) {
      return;
    }
    setRestored(true);
    const snapshot = conflict.restore();
    if (snapshot !== null) {
      // 冲突现场的「我的版本」优先于常规自动保存：后者可能已被后续编辑覆盖。
      draft.replaceValues(snapshot.mine);
    }
  }, [conflict, data, draft, restored]);

  const hiddenRequiredLabels = useMemo(
    () => (data ? hasHiddenRequiredLabels(data.schema) : false),
    [data],
  );

  const validationIssues = useMemo(() => {
    if (data === null || !draft.ready) {
      return [];
    }
    return buildWorkbenchRecords(data, draft.values).issues;
  }, [data, draft.ready, draft.values]);

  const disabledReason = useMemo(() => {
    if (data === null) {
      return "工作台数据尚未加载。";
    }
    if (data.assignment.status !== "IN_PROGRESS") {
      return `当前分配状态为 ${data.assignment.status}，不可填写或提交。`;
    }
    if (!EDITABLE_TASK_STATUSES.has(data.task.status)) {
      return `当前任务状态为 ${data.task.status}，不可填写或提交。`;
    }
    if (!EDITABLE_BATCH_STATUSES.has(data.batch.status)) {
      return `当前批次状态为 ${data.batch.status}，不可填写或提交。`;
    }
    if (!schemaSnapshotMatches(data.task, data.schema)) {
      return "任务冻结的 Schema 快照与当前 Schema 不一致，提交会被服务端拒绝。";
    }
    if (hiddenRequiredLabels) {
      return "Schema 含标注员不可见且必填的标签，无法完成提交。";
    }
    if (data.episodes.length === 0) {
      return "当前分配没有可标注的 Episode。";
    }
    return null;
  }, [data, hiddenRequiredLabels]);

  const phase = resolvePagePhase({
    hasData: data !== null,
    loading: workbench.loading,
    error: workbench.error,
  });

  const handleFieldChange = useCallback(
    (episodeId: string, labelKey: string, value: Parameters<typeof draft.updateField>[2]) => {
      draft.updateField(episodeId, labelKey, value);
    },
    [draft],
  );

  const reloadAll = useCallback(() => {
    workbench.reload();
  }, [workbench]);

  const handleSubmit = useCallback(async () => {
    if (data === null) {
      return;
    }
    setValidationAttempted(true);
    setFeedback(null);
    if (disabledReason !== null) {
      setFeedback({
        kind: "error",
        title: "当前不可提交",
        description: disabledReason,
      });
      return;
    }
    const built = buildWorkbenchRecords(data, draft.values);
    if (built.issues.length > 0) {
      setFeedback({
        kind: "error",
        title: "完整性校验未通过",
        description: `还有 ${built.issues.length} 项需要修正，请按字段提示补齐后再提交。`,
      });
      return;
    }
    if (!online) {
      setFeedback({
        kind: "warning",
        title: "网络离线",
        description: "草稿已保留在本地；恢复联网后再提交。",
      });
      return;
    }
    setBusyAction("submit");
    // 记录本次请求实际使用的 If-Match 版本：冲突兜底提示要用它，
    // 不能等失败后再去猜（那时服务端版本已经变了）。
    let sentSubmissionVersion = 0;
    try {
      const created = await annotationApi.createSubmission(projectId, {
        assignment_id: data.assignment.assignment_id,
        records: built.records,
        data_source: dataSource,
      });
      sentSubmissionVersion = created.version;
      await annotationApi.actOnSubmission(
        projectId,
        created.submission_id,
        "submit",
        { version: created.version },
      );
      draft.resetDraft();
      setFeedback(
        annotationSuccess("提交已冻结，等待复核。本地草稿已清除。"),
      );
      setValidationAttempted(false);
      reloadAll();
    } catch (caught) {
      // 先立即落盘再判断冲突类型，避免后续刷新覆盖最近输入。
      draft.flushDraft();
      const recovered = await conflict.enter(caught, sentSubmissionVersion);
      if (!recovered) {
        setFeedback(describeAnnotationActionError(caught));
        reloadAll();
      }
    } finally {
      setBusyAction(null);
    }
  }, [data, dataSource, disabledReason, draft, online, projectId, reloadAll, conflict]);

  const handleSubmitDraft = useCallback(async () => {
    const submission = data?.existingDraft;
    if (!submission) {
      return;
    }
    setBusyAction("submit-draft");
    setFeedback(null);
    try {
      await annotationApi.actOnSubmission(projectId, submission.submission_id, "submit", {
        version: submission.version,
      });
      setFeedback(annotationSuccess("服务端草稿已提交，等待复核。"));
      reloadAll();
    } catch (caught) {
      // 服务端草稿同样可能被他人推进版本：走同一套冲突恢复流。
      const recovered = await conflict.enter(caught, submission.version);
      if (!recovered) {
        setFeedback(describeAnnotationActionError(caught));
        reloadAll();
      }
    } finally {
      setBusyAction(null);
    }
  }, [data, conflict, projectId, reloadAll]);

  const handleWithdrawDraft = useCallback(async () => {
    const submission = data?.existingDraft;
    if (!submission) {
      return;
    }
    setBusyAction("withdraw-draft");
    setFeedback(null);
    try {
      await annotationApi.actOnSubmission(projectId, submission.submission_id, "withdraw", {
        version: submission.version,
        reason: "工作台撤回草稿以重新填写",
      });
      setFeedback(annotationSuccess("服务端草稿已撤回，可重新填写。"));
      reloadAll();
    } catch (caught) {
      const recovered = await conflict.enter(caught, submission.version);
      if (!recovered) {
        setFeedback(describeAnnotationActionError(caught));
      }
    } finally {
      setBusyAction(null);
    }
  }, [data, conflict, projectId, reloadAll]);

  const title = data ? (
    <Space wrap>
      <span>标注工作台 · {data.task.name}</span>
      {data.task.is_overdue ? <Tag color="red">任务已逾期</Tag> : null}
    </Space>
  ) : (
    "标注工作台"
  );

  const description = data ? (
    <Space wrap size={4}>
      <span>{data.task.code}</span>
      <span>· 分配 {data.assignment.assignment_id}</span>
      <span>· Schema {data.schema.code} v{data.schema.schema_version}</span>
      <span>· 更新于 {formatTimestamp(workbench.updatedAt)}</span>
    </Space>
  ) : (
    "按任务冻结的 Schema 填写标注、保存本地草稿并提交复核。权限与状态以服务端为准。"
  );

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() =>
          navigate(
            data
              ? buildAnnotationTaskDetailPath(projectId, data.task.task_id)
              : buildAnnotationTaskListPath(projectId),
          )
        }
      >
        返回任务
      </Button>
      <Button icon={<ReloadOutlined />} loading={workbench.refreshing} onClick={reloadAll}>
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer title={title} description={description} extra={extra}>
      <Typography.Paragraph type="secondary">
        草稿保存在本机浏览器；提交后版本冻结，不可原地编辑。服务端会校验
        Schema 快照、必填、类型、范围、时间片段与职责分离。
      </Typography.Paragraph>
      <OverdueSummaryCard projectId={projectId} />
      <WorkbenchBody
        projectId={projectId}
        data={data}
        phase={phase}
        error={workbench.error}
        stale={workbench.stale}
        partialErrors={partialErrors}
        online={online}
        values={draft.values}
        draftReady={draft.ready}
        draftNotice={draft.notice}
        saveState={draft.saveState}
        storageAvailable={draft.storageAvailable}
        draftUpdatedAt={draft.updatedAt}
        validationIssues={validationIssues}
        validationAttempted={validationAttempted}
        dataSource={dataSource}
        disabledReason={disabledReason}
        hiddenRequiredLabels={hiddenRequiredLabels}
        busyAction={busyAction}
        feedback={feedback}
        onFieldChange={handleFieldChange}
        onDataSourceChange={setDataSource}
        onSubmit={() => void handleSubmit()}
        onSubmitDraft={() => void handleSubmitDraft()}
        onWithdrawDraft={() => void handleWithdrawDraft()}
        onResetDraft={draft.resetDraft}
        onRetry={reloadAll}
        onBackToList={() =>
          navigate(
            data
              ? buildAnnotationTaskDetailPath(projectId, data.task.task_id)
              : buildAnnotationTaskListPath(projectId),
          )
        }
      />
      {/*
        FR-019 冲突恢复对话框：只在检测到 409 时出现。
        关闭它不会丢数据——打开时快照已落盘，刷新后由恢复 effect 重新进入。
      */}
      <AnnotationConflictModal
        open={recovery.phase !== "idle" && recovery.context !== null}
        context={recovery.context}
        mine={draft.values}
        episodes={data?.episodes ?? []}
        busy={busyAction === "resubmit"}
        initialChoices={recovery.choices}
        onCopyDraft={recovery.copyDraft}
        onApplyMerged={(merged) => void conflict.applyMerged(merged)}
        onRefreshFromServer={() => {
          conflict.discard();
          setFeedback({
            kind: "warning",
            title: "已放弃合并并要求刷新",
            description:
              "本地冲突快照已清除；如需保留输入，请刷新前先复制草稿。服务端内容不会被本页覆盖。",
          });
          reloadAll();
        }}
        onDismiss={recovery.dismiss}
      />
      {recovery.persistenceWarning !== null ? (
        <Typography.Paragraph type="danger">
          {recovery.persistenceWarning}
        </Typography.Paragraph>
      ) : null}
    </PageContainer>
  );
}

export default function AnnotationWorkbenchPage() {
  const projectId = useProjectId();
  const { assignmentId } = useParams<{ assignmentId: string }>();

  if (!projectId) {
    return (
      <PageContainer title="标注工作台" description="标注工作台必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注工作台" />
      </PageContainer>
    );
  }
  if (!assignmentId) {
    return (
      <PageContainer title="标注工作台">
        <ProjectScopeRequired scope="标注工作台" />
      </PageContainer>
    );
  }
  return (
    <WorkbenchContent
      key={`${projectId}:${assignmentId}`}
      projectId={projectId}
      assignmentId={assignmentId}
    />
  );
}
