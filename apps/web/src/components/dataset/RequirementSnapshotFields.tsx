import { Form, Input, InputNumber, Select } from "antd";

const MODALITY_OPTIONS = ["RGB", "DEPTH", "PROPRIOCEPTION", "LANGUAGE", "TACTILE"].map(
  (value) => ({ value }),
);

export default function RequirementSnapshotFields() {
  return (
    <>
      <Form.Item name="business_goal" label="业务目标" rules={[{ required: true, max: 2000 }]}>
        <Input.TextArea rows={2} maxLength={2000} />
      </Form.Item>
      <Form.Item name="model_type" label="模型类型" initialValue="diffusion-policy" rules={[{ required: true }]}>
        <Input maxLength={100} />
      </Form.Item>
      <Form.Item name="training_type" label="训练类型" initialValue="IMITATION" rules={[{ required: true }]}>
        <Select
          options={[
            { value: "IMITATION" },
            { value: "REINFORCEMENT" },
            { value: "SELF_SUPERVISED" },
            { value: "OTHER" },
          ]}
        />
      </Form.Item>
      <Form.Item name="data_modalities" label="数据模态" initialValue={["RGB", "PROPRIOCEPTION"]} rules={[{ required: true }]}>
        <Select mode="multiple" options={MODALITY_OPTIONS} />
      </Form.Item>
      <Form.Item name="scene_targets" label="场景目标（每行一个）" initialValue={["kitchen"]}>
        <Select mode="tags" tokenSeparators={[","]} />
      </Form.Item>
      <Form.Item name="action_targets" label="动作目标（每行一个）" initialValue={["pick"]}>
        <Select mode="tags" tokenSeparators={[","]} />
      </Form.Item>
      <Form.Item name="robot_targets" label="机器人目标" initialValue={["arm-a"]}>
        <Select mode="tags" tokenSeparators={[","]} />
      </Form.Item>
      <Form.Item name="device_targets" label="设备目标" initialValue={["cam-a"]}>
        <Select mode="tags" tokenSeparators={[","]} />
      </Form.Item>
      <Form.Item name="required_labels" label="必需标注标签" initialValue={["action"]}>
        <Select mode="tags" tokenSeparators={[","]} />
      </Form.Item>
      <Form.Item name="target_episode_count" label="目标 Episode 数" initialValue={10} rules={[{ required: true }]}>
        <InputNumber min={1} />
      </Form.Item>
      <Form.Item name="quality_threshold" label="最低质量分" initialValue={0.8}>
        <InputNumber min={0} max={1} step={0.05} />
      </Form.Item>
      <Form.Item name="delivery_due_at" label="交付截止（ISO 时间）" rules={[{ required: true }]}>
        <Input placeholder="2026-12-31T00:00:00Z" />
      </Form.Item>
      <Form.Item name="acceptance_method" label="验收方式" initialValue="离线回放验收" rules={[{ required: true }]}>
        <Input maxLength={500} />
      </Form.Item>
      <Form.Item name="priority" label="优先级" initialValue="P1">
        <Select options={["P0", "P1", "P2", "P3"].map((value) => ({ value }))} />
      </Form.Item>
      <Form.Item name="split_policy_id" label="指定划分策略 ID（可选）">
        <Input placeholder="UUID" />
      </Form.Item>
    </>
  );
}
