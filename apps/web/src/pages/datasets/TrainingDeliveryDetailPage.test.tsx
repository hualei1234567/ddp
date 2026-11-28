import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../services/api";
import { parseTrainingRunEvidenceList } from "../../services/datasetParsers";
import { createTrainingApi } from "../../services/training";
import type { TrainingRunEvidence } from "../../types/dataset";
import { TrainingEvidenceTable } from "./TrainingDeliveryDetailPage";

const PROJECT = "11111111-1111-1111-1111-111111111111";
const DELIVERY = "22222222-2222-2222-2222-222222222222";
const REF = "33333333-3333-3333-3333-333333333333";
const VERSION = "44444444-4444-4444-4444-444444444444";
const USER = "55555555-5555-5555-5555-555555555555";
const EVIDENCE_ID = "66666666-6666-6666-6666-666666666666";
const MODEL_SHA = `sha256:${"a".repeat(64)}`;
const CONFIG_SHA = `sha256:${"b".repeat(64)}`;

const sampleEvidence: TrainingRunEvidence = {
  evidence_id: EVIDENCE_ID,
  project_id: PROJECT,
  external_training_reference_id: REF,
  dataset_version_id: VERSION,
  run_kind: "SMOKE",
  device: "cpu",
  runtime_versions: { python: "3.11.10", torch: "2.5.0" },
  config_sha256: CONFIG_SHA,
  seed: 42,
  batch_size: 2,
  steps: 2,
  started_at: "2026-11-28T12:00:00+00:00",
  finished_at: "2026-11-28T12:00:12+00:00",
  metrics_summary: { gate: "GATE-B-VIDEO", reload_ok: true },
  checkpoint_ref: "/tmp/train_out/checkpoints/000002/pretrained_model",
  checkpoint_sha256: MODEL_SHA,
  exit_code: 0,
  status: "SUCCEEDED",
  stdout_tail: null,
  created_at: "2026-11-28T12:00:13+00:00",
  created_by: USER,
};

describe("TrainingEvidenceTable 展示与解析", () => {
  it("渲染已落库训练证据的 run_kind、步数、model_safetensors_sha256 与状态", () => {
    const html = renderToStaticMarkup(
      <TrainingEvidenceTable
        items={[sampleEvidence]}
        loading={false}
        error={null}
        onReload={() => undefined}
      />,
    );

    expect(html).toContain("已落库训练证据");
    expect(html).toContain("SMOKE 冒烟");
    expect(html).toContain("2 steps");
    expect(html).toContain(MODEL_SHA);
    expect(html).toContain("成功");
    expect(html).toContain("exit_code=0");
  });

  it("失败且无 checkpoint 时显示失败状态与占位符", () => {
    const failedEvidence: TrainingRunEvidence = {
      ...sampleEvidence,
      evidence_id: "77777777-7777-7777-7777-777777777777",
      run_kind: "REAL",
      steps: 100,
      checkpoint_sha256: null,
      metrics_summary: null,
      exit_code: 1,
      status: "FAILED",
    };

    const html = renderToStaticMarkup(
      <TrainingEvidenceTable
        items={[failedEvidence]}
        loading={false}
        error={null}
        onReload={() => undefined}
      />,
    );

    expect(html).toContain("REAL 正式训练");
    expect(html).toContain("100 steps");
    expect(html).toContain("失败");
    expect(html).toContain("exit_code=1");
  });

  it("空列表与错误态分别展示明确提示", () => {
    const emptyHtml = renderToStaticMarkup(
      <TrainingEvidenceTable items={[]} loading={false} error={null} onReload={() => undefined} />,
    );
    expect(emptyHtml).toContain("暂无已落库的训练过程证据");

    const errorHtml = renderToStaticMarkup(
      <TrainingEvidenceTable
        items={[]}
        loading={false}
        error={new ApiError({ kind: "http", status: 500, code: "internal_error", message: "查询失败" })}
        onReload={() => undefined}
      />,
    );
    expect(errorHtml).toContain("查询失败");
  });

  it("parseTrainingRunEvidenceList 与 trainingApi.listDeliveryEvidence 校验项目隔离", async () => {
    const parsed = parseTrainingRunEvidenceList({ items: [sampleEvidence] });
    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0]?.checkpoint_sha256).toBe(MODEL_SHA);

    const getMock = vi.fn(async (_path: string, opts?: { validate?: (v: unknown) => unknown }) => {
      const payload = { items: [sampleEvidence] };
      return opts?.validate ? opts.validate(payload) : payload;
    });
    const api = createTrainingApi({
      get: getMock,
      request: vi.fn(),
    } as never);

    const result = await api.listDeliveryEvidence(PROJECT, DELIVERY);
    expect(result.items).toHaveLength(1);
    expect(getMock).toHaveBeenCalledWith(
      `/api/v1/projects/${PROJECT}/training-deliveries/${DELIVERY}/evidence`,
      expect.any(Object),
    );
  });
});
