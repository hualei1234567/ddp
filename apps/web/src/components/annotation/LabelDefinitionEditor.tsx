import { Button, Card, Col, Form, Input, Row, Select, Space, Switch, Typography } from "antd";
import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";

import { emptyLabelFormValues } from "../../services/annotationSchemaForm";
import {
  ANNOTATION_LABEL_TYPE_VALUES,
  ANNOTATION_LEVEL_VALUES,
  LABEL_VISIBILITY_VALUES,
} from "../../types/annotation";

const labelTypeOptions = ANNOTATION_LABEL_TYPE_VALUES.map((value) => ({
  value,
  label: value,
}));
const levelOptions = ANNOTATION_LEVEL_VALUES.map((value) => ({
  value,
  label: value,
}));
const visibilityOptions = LABEL_VISIBILITY_VALUES.map((value) => ({
  value,
  label: value,
}));

/**
 * Schema 标签定义编辑器（Form.List）。
 *
 * 只负责收集输入；JSON 解析、去重与必填校验在
 * `services/annotationSchemaForm.ts` 统一完成，避免页面各写一套规则。
 */
export default function LabelDefinitionEditor() {
  return (
    <Form.List
      name="labels"
      rules={[
        {
          validator: async (_rule, value: unknown[]) => {
            if (!Array.isArray(value) || value.length === 0) {
              throw new Error("至少需要一个标签定义");
            }
          },
        },
      ]}
    >
      {(fields, { add, remove }, { errors }) => (
        <Space direction="vertical" size={12} className="ddp-stack">
          {fields.map((field, index) => (
            <Card
              key={field.key}
              size="small"
              title={`标签 ${index + 1}`}
              extra={
                <Button
                  type="text"
                  danger
                  icon={<MinusCircleOutlined />}
                  onClick={() => remove(field.name)}
                >
                  移除
                </Button>
              }
            >
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Form.Item
                    name={[field.name, "label_key"]}
                    label="标签键"
                    rules={[{ required: true, message: "请填写标签键" }]}
                  >
                    <Input placeholder="例如 action" maxLength={100} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item
                    name={[field.name, "label_type"]}
                    label="标签类型"
                    rules={[{ required: true, message: "请选择标签类型" }]}
                  >
                    <Select options={labelTypeOptions} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item
                    name={[field.name, "level"]}
                    label="标注层级"
                    rules={[{ required: true, message: "请选择标注层级" }]}
                  >
                    <Select options={levelOptions} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item
                    name={[field.name, "visibility"]}
                    label="可见性"
                    rules={[{ required: true, message: "请选择可见性" }]}
                  >
                    <Select options={visibilityOptions} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item
                    name={[field.name, "required"]}
                    label="必填"
                    valuePropName="checked"
                  >
                    <Switch />
                  </Form.Item>
                </Col>
                <Col xs={24}>
                  <Form.Item name={[field.name, "description"]} label="说明">
                    <Input maxLength={500} />
                  </Form.Item>
                </Col>
                <Col xs={24}>
                  <Form.Item
                    name={[field.name, "value_spec_text"]}
                    label="值定义（JSON 对象，可留空）"
                    tooltip={'例如 {"options": ["pick", "place"]}；仅接受 JSON 对象'}
                  >
                    <Input.TextArea rows={2} placeholder="{}" />
                  </Form.Item>
                </Col>
                <Col xs={24}>
                  <Form.Item
                    name={[field.name, "quality_rules"]}
                    label="质量规则（回车添加，可留空）"
                  >
                    <Select mode="tags" open={false} placeholder="输入后回车" />
                  </Form.Item>
                </Col>
              </Row>
            </Card>
          ))}
          <Button
            block
            icon={<PlusOutlined />}
            onClick={() => add(emptyLabelFormValues())}
          >
            添加标签
          </Button>
          <Form.ErrorList errors={errors} />
          <Typography.Text type="secondary">
            GOLD_ONLY / SYSTEM_ONLY 标签不会在标注工作台展示；定义会随 Schema
            快照写入 Hash。
          </Typography.Text>
        </Space>
      )}
    </Form.List>
  );
}
