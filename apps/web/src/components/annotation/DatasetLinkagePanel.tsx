import { LinkOutlined } from "@ant-design/icons";
import { Alert, Button, Modal, Select, Space, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { buildEpisodeDetailPath } from "../../services/collectionUrl";
import { datasetApi } from "../../services/datasets";
import { datasetDetailPath, datasetListPath } from "../../services/datasetUrl";
import type { AnnotationRecord, AnnotationSubmission } from "../../types/annotation";
import type { Dataset } from "../../types/dataset";

/** 只有复核（或仲裁）通过的提交才满足数据集纳入门禁；服务端仍会再校验一次。 */
const ELIGIBLE_STATUSES: ReadonlySet<AnnotationSubmission["status"]> = new Set([
  "APPROVED",
  "RESOLVED_APPROVED",
]);

/**
 * 审核 → Dataset Builder 的前端闭环。
 *
 * 平台不在这里"直接写一条 DatasetItem"——纳入规则、快照与门禁都在后端。
 * 本面板做三件事：把提交涉及的 Episode 回链出来；确认提交已通过；把
 * Episode 过滤条件带到数据集构建入口，由版本构建按同一套规则生成条目。
 */
export default function DatasetLinkagePanel({
  projectId,
  submission,
  records,
}: {
  projectId: string;
  submission: AnnotationSubmission;
  records: AnnotationRecord[];
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [datasetId, setDatasetId] = useState<string | null>(null);

  const episodeIds = useMemo(
    () => [...new Set(records.map((record) => record.episode_id))],
    [records],
  );
  const eligible = ELIGIBLE_STATUSES.has(submission.status);

  const loadDatasets = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setLoadError(null);
      try {
        const page = await datasetApi.list(projectId, { limit: 50, signal });
        setDatasets(page.items);
      } catch (caught) {
        setLoadError(caught instanceof Error ? caught.message : "数据集加载失败");
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const controller = new AbortController();
    void loadDatasets(controller.signal);
    return () => controller.abort();
  }, [open, loadDatasets]);

  const confirm = () => {
    if (datasetId === null) {
      return;
    }
    // 版本创建表单读取 episode_ids 并预填 selection_filters，
    // 过滤字段本身由后端权威定义，前端不推断纳入规则。
    navigate(datasetDetailPath(datasetId, episodeIds));
    setOpen(false);
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text type="secondary">
        提交涉及的 Episode 可直接回链核对；复核通过后，把该 Episode
        带到数据集构建入口生成版本条目。纳入门禁、快照与 Hash 由服务端强制。
      </Typography.Text>
      <Space wrap>
        {episodeIds.map((episodeId) => (
          <Link key={episodeId} to={buildEpisodeDetailPath(projectId, episodeId)}>
            <Typography.Text code>{episodeId}</Typography.Text>
          </Link>
        ))}
      </Space>
      <Space wrap>
        <Button
          type="primary"
          icon={<LinkOutlined />}
          disabled={!eligible || episodeIds.length === 0}
          onClick={() => setOpen(true)}
        >
          加入数据集
        </Button>
        <Link to={datasetListPath()}>查看全部数据集</Link>
      </Space>
      {!eligible ? (
        <Alert
          type="info"
          showIcon
          message="复核通过后才能加入数据集"
          description="当前提交尚未通过复核；数据集构建只接受 APPROVED / RESOLVED_APPROVED 的标注记录。"
        />
      ) : null}
      <Modal
        title="加入数据集"
        open={open}
        okText="确认加入"
        cancelText="取消"
        okButtonProps={{ disabled: datasetId === null }}
        onOk={confirm}
        onCancel={() => setOpen(false)}
      >
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text>
            选择目标数据集，将跳转到它的构建入口并预填 Episode 过滤条件
            {`（${episodeIds.length} 个）`}。
          </Typography.Text>
          <Select
            aria-label="目标数据集"
            style={{ width: "100%" }}
            loading={loading}
            value={datasetId ?? undefined}
            onChange={(value: string) => setDatasetId(value)}
            placeholder="选择目标数据集"
            options={datasets.map((item) => ({
              value: item.dataset_id,
              label: `${item.name}（${item.code}）`,
            }))}
            notFoundContent={loading ? "加载中" : "暂无可加入的数据集"}
          />
          {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
          {!loading && loadError === null && datasets.length === 0 ? (
            <Typography.Text type="secondary">
              当前项目还没有数据集，请先在数据集页面创建。
            </Typography.Text>
          ) : null}
        </Space>
      </Modal>
    </Space>
  );
}
