import { describe, expect, it } from "vitest";

import {
  annotationSubmissionFixture,
  doubleAnnotationConsistencyFixture,
} from "./__fixtures__/annotationFixtures";
import {
  parseAnnotationSubmission,
  parseDoubleAnnotationConsistency,
} from "./annotationRecordParsers";

describe("双标一致性响应解析", () => {
  it("严格解析一致性结论", () => {
    const consistency = parseDoubleAnnotationConsistency(
      doubleAnnotationConsistencyFixture,
    );
    expect(consistency.review_mode).toBe("DOUBLE");
    expect(consistency.status).toBe("PASSED");
    expect(consistency.value).toBe(1);
  });

  it("未知 review_mode 被拒绝", () => {
    expect(() =>
      parseDoubleAnnotationConsistency({
        ...doubleAnnotationConsistencyFixture,
        review_mode: "TRIPLE",
      }),
    ).toThrow();
  });

  it("提交响应中的 consistency 可缺省，也可解析", () => {
    const withoutConsistency = parseAnnotationSubmission(
      annotationSubmissionFixture,
    );
    expect(withoutConsistency.consistency).toBeNull();

    const withConsistency = parseAnnotationSubmission({
      ...annotationSubmissionFixture,
      consistency: doubleAnnotationConsistencyFixture,
    });
    expect(withConsistency.consistency?.status).toBe("PASSED");
  });
});
