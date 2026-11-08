import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Space,
  Steps,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import CollectionStatusTag from "../../../components/collection/CollectionStatusTag";
import TaskActionBar from "../../../components/collection/TaskActionBar";
import UploadQueue from "../../../components/collection/UploadQueue";
import WorkbenchChecklist from "../../../components/collection/WorkbenchChecklist";
import ApiErrorState from "../../../components/ui/ApiErrorState";
import PageState from "../../../components/ui/PageState";
import type { UseUploadQueueResult } from "../../../hooks/useUploadQueue";
import type { ApiError } from "../../../services/api";
import {
  getTaskStatus,
  resolveServerActions,
} from "../../../services/collectionPresentation";
import {
  buildTaskDetailPath,
  WORKBENCH_STEP_VALUES,
  type TaskListFilters,
  type WorkbenchStep,
} from "../../../services/collectionUrl";
import type { PagePhase } from "../../../services/pagePhase";
import type { TaskActionFeedback } from "../../../services/taskActionFeedback";
import {
  allSelfChecksPassed,
  canUploadForTaskStatus,
  SELF_CHECK_ITEMS,
  summarizeChecklist,
  WORKBENCH_CHECK_ITEMS,
  type CheckResult,
  type CheckResults,
} from "../../../services/workbenchChecklist";
import type { CollectionTask } from "../../../types/collection";
import SelfCheckStep from "./SelfCheckStep";
import TaskConfirmStep from "./TaskConfirmStep";

const STEP_ITEMS = [
  { title: "任务确认", description: "核对任务与版本" },
  { title: "开工检查", description: "阻塞项必须通过" },
  { title: "采集", description: "开始/暂停/异常" },
  { title: "上传", description: "断点续传与校验" },
  { title: "自检提交", description: "返工与提交" },
] as const;

const CAPTURE_ACTIONS = new Set(["pause", "resume", "block", "unblock"]);

function stepIndex(step: WorkbenchStep): number {
  return WORKBENCH_STEP_VALUES.indexOf(step);
}

export interface WorkbenchBodyProps {
  projectId: string;
  taskId: string;
  filters: TaskListFilters;
  data: CollectionTask | null;
  phase: PagePhase;
  error: ApiError | null;
  stale: boolean;
  step: WorkbenchStep;
  online: boolean;
  actions: ReturnType<typeof resolveServerActions>;
  checklistResults: CheckResults;
  selfCheckResults: CheckResults;
  uploadQueue: UseUploadQueueResult;
  busyAction: string | null;
  feedback: TaskActionFeedback | null;
  isMobile: boolean;
  onStepChange: (step: WorkbenchStep) => void;
  onCheckChange: (key: string, result: CheckResult) => void;
  onRecheck: () => void;
  onSelfCheckChange: (key: string, passed: boolean) => void;
  onAction: (action: string, reason: string | null) => void;
  onRetry: () => void;
  onBackToList: () => void;
  now?: number;
}

export default function WorkbenchBody({
  projectId,
  taskId,
  filters,
  data,
  phase,
  error,
  stale,
  step,
  online,
  actions,
  checklistResults,
  selfCheckResults,
  uploadQueue,
  busyAction,
  feedback,
  isMobile,
  onStepChange,
  onCheckChange,
  onRecheck,
  onSelfCheckChange,
  onAction,
  onRetry,
  onBackToList,
  now = Date.now(),
}: WorkbenchBodyProps) {
  const checkSummary = useMemo(
    () => summarizeChecklist(WORKBENCH_CHECK_ITEMS, checklistResults),
    [checklistResults],
  );
  const selfReady = allSelfChecksPassed(SELF_CHECK_ITEMS, selfCheckResults);
  const actionSet = useMemo(
    () => new Set<string>(actions.map((entry) => entry.action)),
    [actions],
  );

  if (phase === "forbidden") {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (phase === "not-found") {
    return (
      <PageState
        variant="empty"
        title="任务不存在或无权进入工作台"
        description="服务端未找到该任务；跨项目资源统一不可见。"
        action={<Button onClick={onBackToList}>返回任务列表</Button>}
      />
    );
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载现场工作台"
        description="正在读取任务、SOP 快照与可执行动作。"
      />
    );
  }
  if (phase === "error") {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (!data) {
    return <PageState variant="empty" title="暂无工作台任务" />;
  }

  const rework = data.status === "REWORK" || data.status === "REJECTED";
  const startDisabledReason = !online
    ? "离线时不允许开始新任务；恢复网络后重试。"
    : !actionSet.has("start")
      ? "服务端未下发 start 动作；前端不根据状态自行推断。"
      : !checkSummary.ready
        ? `还有未通过的阻塞项：${checkSummary.blockingRemaining.join("、")}。`
        : null;
  const submitDisabledReason = !online
    ? "离线时不允许提交；自检草稿与上传队列保留在本地。"
    : !actionSet.has("submit")
      ? "服务端未下发 submit 动作；前端不根据状态自行推断。"
      : !selfReady
        ? "自检清单尚未全部完成。"
        : null;
  const uploadDisabledReason = canUploadForTaskStatus(data.status)
    ? null
    : `当前任务状态（${getTaskStatus(data.status).label}）不允许上传；以服务端为准。`;
  const captureActions = actions.filter((entry) =>
    CAPTURE_ACTIONS.has(entry.action),
  );

  const stepContent: ReactNode =
    step === "confirm" ? (
      <TaskConfirmStep task={data} />
    ) : step === "check" ? (
      <WorkbenchChecklist
        items={WORKBENCH_CHECK_ITEMS}
        results={checklistResults}
        onChange={onCheckChange}
        onRecheck={onRecheck}
      />
    ) : step === "capture" ? (
      <Space direction="vertical" size={12} className="ddp-stack">
        <Alert
          type={data.status === "IN_PROGRESS" ? "success" : "info"}
          showIcon
          message={
            data.status === "IN_PROGRESS"
              ? "正在采集"
              : `当前状态：${getTaskStatus(data.status).label}`
          }
          description="录制由机器人/采集设备完成；工作台只记录任务动作、异常与上传。状态变化以服务端返回为准。"
        />
        {captureActions.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="服务端未下发可执行采集动作"
            description="allowed_actions 缺失或未包含 pause/resume/block/unblock；页面不推断动作。"
          />
        ) : (
          <TaskActionBar
            actions={captureActions}
            busyAction={busyAction}
            disabledReason={!online ? "离线时不允许发起任务动作。" : null}
            onAction={onAction}
          />
        )}
        <Tooltip title="异常上报接口未在 DDP-301 草案中定义">
          <span>
            <Button icon={<WarningOutlined />} disabled>
              异常上报
            </Button>
          </span>
        </Tooltip>
      </Space>
    ) : step === "upload" ? (
      <UploadQueue
        items={uploadQueue.items}
        online={online}
        disabledReason={uploadDisabledReason}
        onAddFiles={uploadQueue.addFiles}
        onPause={uploadQueue.pause}
        onResume={uploadQueue.resume}
        onRetry={uploadQueue.retry}
        onCancel={uploadQueue.cancel}
        onRefreshVerification={uploadQueue.refreshVerification}
        onRemove={uploadQueue.remove}
        now={now}
      />
    ) : (
      <SelfCheckStep
        results={selfCheckResults}
        onChange={onSelfCheckChange}
        submitDisabledReason={submitDisabledReason}
        submitting={busyAction === "submit"}
        online={online}
        onSubmit={() => onAction("submit", null)}
      />
    );

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {!online ? (
        <Alert
          type="warning"
          showIcon
          message="离线"
          description="本地检查草稿与当前队列保留；不允许离线开始新任务、提交或审批。恢复网络后可继续断点续传。"
        />
      ) : null}
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的任务数据；状态可能已变化。"
          onRetry={onRetry}
        />
      ) : null}
      {feedback ? (
        <Alert
          type={feedback.kind}
          showIcon
          message={feedback.title}
          description={feedback.description}
          action={
            feedback.kind === "warning" ? (
              <Button size="small" onClick={onRetry}>
                刷新任务
              </Button>
            ) : undefined
          }
        />
      ) : null}
      {rework ? (
        <Alert
          type="error"
          showIcon
          message="返工要求（置顶）"
          description={
            data.status_reason ??
            "服务端未返回返工要求文本；请刷新或联系质检确认问题项、范围与截止时间，不得自行猜测。"
          }
        />
      ) : null}

      <Card>
        <Space direction="vertical" size={12} className="ddp-stack">
          <Space wrap>
            <Typography.Title level={4} className="ddp-title">
              {data.name}
            </Typography.Title>
            <CollectionStatusTag kind="task" value={data.status} />
            <CollectionStatusTag kind="priority" value={data.priority} />
            <Tag color="blue">SOP {data.sop_version ?? "未记录"}</Tag>
            <Tag>上传队列 {uploadQueue.items.length} 个文件</Tag>
            <Tag color={online ? "green" : "orange"}>
              {online ? "网络在线" : "网络离线"}
            </Tag>
          </Space>
          <Steps
            size={isMobile ? "small" : "default"}
            direction={isMobile ? "vertical" : "horizontal"}
            current={stepIndex(step)}
            items={STEP_ITEMS.map((item) => ({
              title: item.title,
              description: item.description,
            }))}
            onChange={(index) => {
              const next = WORKBENCH_STEP_VALUES[index];
              if (next) {
                onStepChange(next);
              }
            }}
          />
        </Space>
      </Card>

      <Card title={STEP_ITEMS[stepIndex(step)]?.title ?? "工作台"}>
        {stepContent}
      </Card>

      <div className="ddp-workbench-footer">
        <Space wrap>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => {
              const index = stepIndex(step);
              const previous = WORKBENCH_STEP_VALUES[index - 1];
              if (previous) {
                onStepChange(previous);
              }
            }}
            disabled={step === "confirm"}
          >
            上一步
          </Button>
          {step === "confirm" ? (
            <Button type="primary" onClick={() => onStepChange("check")}>
              开始开工检查
            </Button>
          ) : null}
          {step === "check" ? (
            <Tooltip title={startDisabledReason ?? ""}>
              <span>
                <Button
                  type="primary"
                  size="large"
                  className="ddp-touch-target"
                  disabled={Boolean(startDisabledReason)}
                  loading={busyAction === "start"}
                  onClick={() => onAction("start", null)}
                >
                  开始采集
                </Button>
              </span>
            </Tooltip>
          ) : null}
          {step === "capture" ? (
            <Button
              type="primary"
              icon={<ArrowRightOutlined />}
              onClick={() => onStepChange("upload")}
            >
              进入上传
            </Button>
          ) : null}
          {step === "upload" ? (
            <Button
              type="primary"
              icon={<ArrowRightOutlined />}
              onClick={() => onStepChange("submit")}
            >
              进入自检提交
            </Button>
          ) : null}
          {step === "submit" ? (
            <Typography.Text type="secondary">
              {submitDisabledReason ??
                "提交按钮位于上方自检清单；提交后等待数据检查。"}
            </Typography.Text>
          ) : null}
          <Link to={buildTaskDetailPath(projectId, taskId, filters)}>
            <Button>查看任务详情</Button>
          </Link>
        </Space>
        {step === "check" && startDisabledReason ? (
          <Typography.Text type="secondary">
            {startDisabledReason}
          </Typography.Text>
        ) : null}
      </div>
    </Space>
  );
}
