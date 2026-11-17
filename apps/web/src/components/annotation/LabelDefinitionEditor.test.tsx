import { Form } from "antd";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { emptyLabelFormValues } from "../../services/annotationSchemaForm";
import LabelDefinitionEditor from "./LabelDefinitionEditor";

function renderEditor(withLabel = false): string {
  return renderToStaticMarkup(
    <Form
      initialValues={{
        labels: withLabel
          ? [{ ...emptyLabelFormValues(), label_key: "action" }]
          : [],
      }}
    >
      <LabelDefinitionEditor />
    </Form>,
  );
}

describe("Schema 标签定义编辑器", () => {
  it("空定义时可添加标签", () => {
    const html = renderEditor();
    expect(html).toContain("添加标签");
  });

  it("已有标签时渲染标签键、类型、层级与可见性输入", () => {
    const html = renderEditor(true);
    expect(html).toContain("标签键");
    expect(html).toContain("标签类型");
    expect(html).toContain("标注层级");
    expect(html).toContain("可见性");
    expect(html).toContain("移除");
  });
});
