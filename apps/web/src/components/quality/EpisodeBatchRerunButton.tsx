import { ReloadOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useCallback, useMemo, useState } from "react";

import { qualityRunApi } from "../../services/qualityRuns";
import type { QualityRerunSubject } from "./QualityBatchRerunModal";
import QualityBatchRerunModal from "./QualityBatchRerunModal";

/**
 * Episode 范围的批量重跑入口（FR-026 / FR-028）。
 *
 * 自治容器：打开对话框时逐对象拉取**最近一次运行**（服务端按创建时间
 * 倒序，第一页首条即最近），把结论作为 `lastStatus` 交给对话框——
 * FR-028「只重试失败项」需要这个信息。查询失败按「无结论」降级处理，
 * 不阻断预览；真正的对象校验由服务端在入队时执行。
 *
 * 列表页传 `selectedIds`（行选择）；详情页可传 `subjects` 直接注入
 * 已知结论（省一次查询）。
 */
export default function EpisodeBatchRerunButton({
  projectId,
  selectedIds,
  subjects,
  disabled = false,
  onQueued,
}: {
  /** 当前勾选的对象；为空时按钮禁用。 */
  selectedIds: string[];
  projectId: string;
  /** 可选：直接提供结论信息（详情页场景），跳过逐对象查询。 */
  subjects?: QualityRerunSubject[];
  disabled?: boolean;
  /** 入队成功后的回调（页面刷新提示等）。 */
  onQueued?: (count: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState<QualityRerunSubject[] | null>(null);

  const uniqueIds = useMemo(() => [...new Set(selectedIds)], [selectedIds]);

  const openModal = useCallback(async () => {
    setOpen(true);
    if (subjects !== undefined) {
      // 详情页直接给定结论，不需要逐对象查询。
      setFetched(subjects);
      return;
    }
    setLoading(true);
    setFetched(null);
    try {
      const results = await Promise.all(
        uniqueIds.map(async (subjectId) => {
          try {
            const page = await qualityRunApi.listRuns(projectId, {
              subject_type: "EPISODE",
              subject_id: subjectId,
              limit: 1,
            });
            return {
              subjectId,
              lastStatus: page.items[0]?.status ?? null,
            } satisfies QualityRerunSubject;
          } catch {
            // 查询失败（含对象还没有任何运行的 404）按「无结论」降级：
            // 预览与全量重跑不受影响，只影响失败项筛选的可用性。
            return { subjectId, lastStatus: null } satisfies QualityRerunSubject;
          }
        }),
      );
      setFetched(results);
    } finally {
      setLoading(false);
    }
  }, [projectId, subjects, uniqueIds]);

  const rerun = useMemo(
    () =>
      async (subjectIds: string[]) => {
        const queued = await qualityRunApi.batchCreateRuns(projectId, {
          subject_type: "EPISODE",
          subject_ids: subjectIds,
        });
        onQueued?.(queued.length);
        return queued;
      },
    [projectId, onQueued],
  );

  const modalSubjects = loading ? [] : (fetched ?? []);
  const scopeLabel = loading
    ? "正在读取最近一次质检结论…"
    : subjects !== undefined
      ? "当前 Episode"
      : `已选择的 ${uniqueIds.length} 条 Episode`;

  return (
    <>
      <Button
        icon={<ReloadOutlined />}
        disabled={disabled || uniqueIds.length === 0}
        onClick={() => void openModal()}
      >
        批量重跑{uniqueIds.length > 0 ? `（${uniqueIds.length}）` : ""}
      </Button>
      <QualityBatchRerunModal
        open={open}
        subjects={modalSubjects}
        scopeLabel={scopeLabel}
        rerun={rerun}
        onClose={() => {
          setOpen(false);
          setFetched(null);
        }}
      />
    </>
  );
}
