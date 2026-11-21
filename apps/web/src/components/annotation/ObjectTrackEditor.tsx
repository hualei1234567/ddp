/**
 * OBJECT_TRACK 结构化编辑器（V31-023）。
 *
 * 为什么要替换原始 JSON TextArea：OBJECT_TRACK 是标注员最高频的输入（每个
 * 目标物体一条轨迹），手写 JSON 既容易写坏，也没法把时间轴上的框选直接转成
 * 一条轨迹。这里提供「行」级编辑：起止时间可手填，也可以直接用时间轴选区
 * 新增一行；除起止时间外的字段（track_id / bbox / 自定义属性）原样保留。
 *
 * 写回规则：只有全部行都通过 `objectTrackRowIssue` 才写回 JSON 字符串，
 * 草稿的存储契约（字符串）与既有校验链路保持不变；任何一行非法时保留原文
 * 并在本地提示，不把半成品写进草稿让提交环节"看起来通过"。
 *
 * 若现有值无法结构化（历史数据是别的形状），退回只读原文 + 原始 TextArea，
 * 不擅自改写用户已有数据。
 */
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Input, Space, Table, Typography } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  objectTrackRowIssue,
  parseObjectTrackDraft,
  serializeObjectTrackDraft,
  type ObjectTrackRow,
} from "../../services/objectTrackDraft";
import type { LabelDefinition } from "../../types/annotation";

interface RowState extends ObjectTrackRow {
  /** 仅用于 React key，不写回。 */
  key: string;
}

let nextKey = 0;
function withKey(row: ObjectTrackRow): RowState {
  nextKey += 1;
  return { ...row, key: `track-row-${nextKey}` };
}

export interface ObjectTrackEditorProps {
  definition: LabelDefinition;
  value: string;
  disabled: boolean;
  onChange: (text: string) => void;
  /** 时间轴当前选区（纳秒）；提供时显示「用选区新增轨迹」。 */
  pendingRangeNs?: { startNs: number; endNs: number } | null;
}

export default function ObjectTrackEditor({
  definition,
  value,
  disabled,
  onChange,
  pendingRangeNs,
}: ObjectTrackEditorProps) {
  const spec = definition.value_spec;
  const initial = useMemo(() => parseObjectTrackDraft(value), [value]);
  const [rows, setRows] = useState<RowState[]>(() =>
    initial.rows.map(withKey),
  );
  const [parseError, setParseError] = useState<string | null>(initial.error);
  const lastEmitted = useRef<string | null>(null);

  // 外部改动（重置草稿、切换 Episode）时重新解析；自己刚写回的字符串跳过，
  // 避免每次击键都把行重建一遍。
  useEffect(() => {
    if (value === lastEmitted.current) {
      return;
    }
    const parsed = parseObjectTrackDraft(value);
    setRows(parsed.rows.map(withKey));
    setParseError(parsed.error);
  }, [value]);

  const issues = useMemo(
    () => rows.map((row) => objectTrackRowIssue(row, spec)),
    [rows, spec],
  );

  const commit = (next: RowState[]) => {
    setRows(next);
    if (next.length === 0) {
      lastEmitted.current = "";
      onChange("");
      return;
    }
    if (next.some((row) => objectTrackRowIssue(row, spec) !== null)) {
      // 有非法行：保留本地状态，不写回草稿。
      return;
    }
    const text = serializeObjectTrackDraft(next);
    lastEmitted.current = text;
    onChange(text);
  };

  const updateRow = (key: string, patch: Partial<ObjectTrackRow>) => {
    commit(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const addRow = () => {
    const startNs =
      pendingRangeNs !== undefined && pendingRangeNs !== null
        ? String(pendingRangeNs.startNs)
        : "";
    const endNs =
      pendingRangeNs !== undefined && pendingRangeNs !== null
        ? String(pendingRangeNs.endNs)
        : "";
    commit([...rows, withKey({ startNs, endNs, extra: {} })]);
  };

  const removeRow = (key: string) => {
    commit(rows.filter((row) => row.key !== key));
  };

  if (parseError !== null) {
    return (
      <Space direction="vertical" size={6} className="ddp-stack">
        <Alert
          type="warning"
          showIcon
          message="现有值无法结构化编辑"
          description={`${parseError}。为避免丢数据，这里保留原始文本；清空后可用结构化编辑器重新录入。`}
        />
        <Input.TextArea
          aria-label={definition.label_key}
          value={value}
          disabled={disabled}
          rows={4}
          onChange={(event) => onChange(event.target.value)}
        />
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Table<RowState>
        size="small"
        pagination={false}
        rowKey="key"
        dataSource={rows}
        locale={{ emptyText: "还没有轨迹行；可新增一行或先在时间轴上框选。" }}
        columns={[
          {
            title: "开始（ns）",
            dataIndex: "startNs",
            render: (_: unknown, row: RowState) => (
              <Input
                aria-label={`轨迹 ${row.key} 开始时间`}
                value={row.startNs}
                disabled={disabled}
                onChange={(event) =>
                  updateRow(row.key, { startNs: event.target.value })
                }
              />
            ),
          },
          {
            title: "结束（ns）",
            dataIndex: "endNs",
            render: (_: unknown, row: RowState) => (
              <Input
                aria-label={`轨迹 ${row.key} 结束时间`}
                value={row.endNs}
                disabled={disabled}
                onChange={(event) =>
                  updateRow(row.key, { endNs: event.target.value })
                }
              />
            ),
          },
          {
            title: "其他字段",
            render: (_: unknown, row: RowState) => {
              const keys = Object.keys(row.extra);
              return keys.length === 0 ? (
                <Typography.Text type="secondary">无</Typography.Text>
              ) : (
                <Typography.Text code>{keys.join(", ")}</Typography.Text>
              );
            },
          },
          {
            title: "校验",
            width: 200,
            render: (_: unknown, row: RowState) => {
              const index = rows.findIndex((item) => item.key === row.key);
              const issue = issues[index];
              return issue === null ? (
                <Typography.Text type="success">合法</Typography.Text>
              ) : (
                <Typography.Text type="danger">{issue}</Typography.Text>
              );
            },
          },
          {
            title: "操作",
            width: 80,
            render: (_: unknown, row: RowState) => (
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                aria-label={`删除轨迹 ${row.key}`}
                disabled={disabled}
                onClick={() => removeRow(row.key)}
              />
            ),
          },
        ]}
      />
      <Space wrap size={8}>
        <Button
          size="small"
          icon={<PlusOutlined />}
          disabled={disabled}
          onClick={addRow}
        >
          新增轨迹
        </Button>
        {pendingRangeNs ? (
          <Typography.Text type="secondary">
            新增时会带入时间轴当前选区
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">
            时间轴上的选区可一键带入起止时间。
          </Typography.Text>
        )}
      </Space>
      {rows.length > 0 && issues.some((issue) => issue !== null) ? (
        <Typography.Text type="secondary">
          存在非法行时不会写入草稿；修正后自动同步。
        </Typography.Text>
      ) : null}
    </Space>
  );
}
