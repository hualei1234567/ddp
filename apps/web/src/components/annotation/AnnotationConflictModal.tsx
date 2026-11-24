import { Alert, Button, Modal, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useMemo, useState } from "react";

import {
  diffFieldKey,
  diffLabelValues,
  mergeByChoice,
  summarizeDiff,
  type ConflictContext,
  type LabelDiff,
  type MergeChoice,
} from "../../services/annotationConflict";
import type { EpisodeFormValues } from "../../services/annotationValidation";
import type { Episode } from "../../types/collection";

/**
 * 标注冲突恢复对话框（FR-019）。
 *
 * ## 语义红线：这里不做自动合并
 *
 * 审计原话是「『复制草稿/人工合并』的完整恢复流未做」。本组件把恢复流拆成
 * 三个**用户显式动作**，任何一步都不会自动替用户决定：
 *
 * 1. **提示**：明确写出「他人已更新此标注（当前版本 N，你基于版本 M）」，
 *    并说明本地输入**没有被丢弃**；
 * 2. **复制草稿**：一键复制全部用户输入为 JSON（剪贴板不可用时降级为下载
 *    文件），用于线下比对或提工单；
 * 3. **人工合并**：逐项列出差异，用户对每一项选择「保留我的」或「采用服务端」，
 *    未选择的一律保留我的值；确认后才把合并结果交回页面，由页面用
 *    **最新版本**重新提交。
 *
 * 关闭弹窗**不会**丢失任何数据：调用方在打开弹窗时已把快照落盘
 * （`annotationConflictDraft.ts`），刷新后重新进入仍能恢复。
 */
export default function AnnotationConflictModal({
  open,
  context,
  mine,
  episodes,
  busy,
  onCopyDraft,
  onApplyMerged,
  onRefreshFromServer,
  onDismiss,
  initialChoices,
}: {
  open: boolean;
  /** 冲突上下文：两侧版本 + 服务端最新提交（不可比内容时为 null）。 */
  context: ConflictContext | null;
  /** 冲突发生时我的全部表单值。 */
  mine: Record<string, EpisodeFormValues>;
  /** 当前工作台 Episode，仅用于把 ID 显示成 code，不参与合并。 */
  episodes: readonly Episode[];
  /** 正在用合并结果重新提交。 */
  busy: boolean;
  /**
   * 复制/下载草稿。由调用方提供完整导出（含项目/分配变量），本组件只负责触发；
   * 返回 false 表示剪贴板不可用（调用方已降级为下载文件）。
   */
  onCopyDraft: (method: "clipboard" | "download") => Promise<boolean>;
  /** 用户确认合并结果，页面负责按 currentVersion 重新提交。 */
  onApplyMerged: (merged: Record<string, EpisodeFormValues>) => void;
  /** 放弃合并、改为刷新服务端最新内容（仍会先复制草稿保底）。 */
  onRefreshFromServer: () => void;
  /** 关闭对话框（不丢数据：快照仍在本地，下次进入可恢复）。 */
  onDismiss: () => void;
  /** 刷新后恢复：继续沿用用户此前已做出的合并选择。 */
  initialChoices?: Record<string, MergeChoice>;
}) {
  const [choices, setChoices] = useState<Record<string, MergeChoice>>(initialChoices ?? {});
  const [notice, setNotice] = useState<string | null>(null);

  const serverValues = context?.serverValues ?? null;

  const diffs = useMemo(
    () => (serverValues === null ? [] : diffLabelValues(mine, serverValues)),
    [mine, serverValues],
  );
  const summary = useMemo(() => summarizeDiff(diffs), [diffs]);

  const episodeLabel = useMemo(() => {
    const byId = new Map(episodes.map((item) => [item.episode_id, item.code]));
    return (episodeId: string) => byId.get(episodeId) ?? episodeId;
  }, [episodes]);

  if (context === null) {
    return null;
  }

  const setChoice = (diff: LabelDiff, choice: MergeChoice) => {
    setChoices((previous) => ({ ...previous, [diffFieldKey(diff)]: choice }));
  };

  const chosenServerCount = Object.values(choices).filter(
    (value) => value === "server",
  ).length;

  const handleCopy = async () => {
    setNotice(null);
    const copied = await onCopyDraft("clipboard");
    setNotice(
      copied
        ? "已复制草稿 JSON（含全部输入与差异清单）。"
        : "剪贴板不可用，草稿已作为 JSON 文件下载。",
    );
  };

  const handleDownload = async () => {
    setNotice(null);
    await onCopyDraft("download");
    setNotice("草稿已下载为 JSON 文件。");
  };

  const handleApply = () => {
    if (serverValues === null) {
      return;
    }
    onApplyMerged(mergeByChoice(mine, serverValues, choices));
  };

  const columns: TableColumnsType<LabelDiff> = [
    {
      title: "Episode",
      dataIndex: "episodeId",
      width: 160,
      render: (value: string) => episodeLabel(value),
    },
    {
      title: "标签",
      dataIndex: "labelKey",
      width: 180,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "差异",
      dataIndex: "kind",
      width: 90,
      render: (value: LabelDiff["kind"]) => {
        const meta = {
          added: { color: "green", label: "服务端新增" },
          removed: { color: "red", label: "我这有" },
          changed: { color: "orange", label: "值不同" },
        }[value];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "我的版本",
      dataIndex: "mine",
      render: (value: LabelDiff["mine"]) => describeValue(value),
    },
    {
      title: "服务端最新",
      dataIndex: "server",
      render: (value: LabelDiff["server"]) => describeValue(value),
    },
    {
      title: "我的选择",
      key: "choice",
      width: 190,
      render: (_, diff) => {
        const selected = choices[diffFieldKey(diff)] ?? "mine";
        return (
          <Space size={4}>
            <Button
              size="small"
              type={selected === "mine" ? "primary" : "default"}
              onClick={() => setChoice(diff, "mine")}
            >
              保留我的
            </Button>
            <Button
              size="small"
              type={selected === "server" ? "primary" : "default"}
              onClick={() => setChoice(diff, "server")}
            >
              采用服务端
            </Button>
          </Space>
        );
      },
    },
  ];

  const comparable = serverValues !== null;

  return (
    <Modal
      open={open}
      title={`标注冲突：他人已更新此标注（当前版本 ${context.currentVersion}，你基于版本 ${context.expectedVersion}）`}
      width={1080}
      maskClosable={false}
      keyboard={false}
      closable={!busy}
      onCancel={onDismiss}
      footer={[
        <Button key="copy" onClick={() => void handleCopy()}>
          复制草稿
        </Button>,
        <Button key="download" onClick={() => void handleDownload()}>
          下载 JSON
        </Button>,
        <Button key="refresh" onClick={onRefreshFromServer} disabled={busy}>
          放弃合并并刷新
        </Button>,
        <Button
          key="apply"
          type="primary"
          loading={busy}
          disabled={!comparable}
          onClick={handleApply}
        >
          用合并结果重新提交
        </Button>,
      ]}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          message="你的输入没有被丢弃"
          description={
            <span>
              服务端版本已前进：你的草稿基于版本 {context.expectedVersion}，
              服务端当前是版本 {context.currentVersion}。本地输入已保存在本机，
              关闭本对话框或刷新页面都不会丢失；重新进入可继续合并。
            </span>
          }
        />

        {notice !== null ? (
          <Alert type="info" showIcon message={notice} />
        ) : null}

        {!comparable ? (
          <Alert
            type="info"
            showIcon
            message="无法展示逐项差异"
            description="服务端未返回可对比的标注内容（可能无记录或盲审脱敏）。请先「复制草稿」保留你的输入，再「放弃合并并刷新」基于服务端最新状态重新填写；本页不会自动合并或覆盖任何内容。"
          />
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message={`共 ${summary.total} 项差异：服务端新增 ${summary.added}、仅我有 ${summary.removed}、值不同 ${summary.changed}`}
              description={
                <span>
                  默认全部「保留我的」；点「采用服务端」的项会被替换。
                  已选择采用服务端 {chosenServerCount} 项。合并只是页面上的本地决定，
                  不会自动写服务端——只有点「用合并结果重新提交」并成功才算落库。
                </span>
              }
            />
            <Table<LabelDiff>
              size="small"
              rowKey={(diff) => diffFieldKey(diff)}
              columns={columns}
              dataSource={diffs}
              pagination={false}
              scroll={{ y: 420 }}
              locale={{ emptyText: "两侧标签值完全一致，可直接用当前版本重新提交。" }}
            />
          </>
        )}
      </Space>
    </Modal>
  );
}

function describeValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "—";
  }
  if (typeof value === "string") {
    return value.trim() === "" ? "—" : value;
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "—" : value.join("、");
  }
  if (
    typeof value === "object" &&
    typeof (value as { start?: unknown }).start === "string"
  ) {
    const range = value as { start: string; end: string };
    return `${range.start} – ${range.end} ns`;
  }
  return JSON.stringify(value);
}
