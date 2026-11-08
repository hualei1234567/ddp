import { Alert } from "antd";

import type { AnnotationFeedback } from "../../services/annotationPresentation";

const ALERT_TYPE = {
  success: "success",
  warning: "warning",
  error: "error",
} as const;

interface AnnotationFeedbackAlertProps {
  feedback: AnnotationFeedback | null;
}

/** 操作反馈：保留错误码/request_id，职责分离与黄金集访问使用专用文案。 */
export default function AnnotationFeedbackAlert({
  feedback,
}: AnnotationFeedbackAlertProps) {
  if (feedback === null) {
    return null;
  }
  return (
    <Alert
      type={ALERT_TYPE[feedback.kind]}
      showIcon
      message={feedback.title}
      description={feedback.description}
    />
  );
}
