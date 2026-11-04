"use client";

import React from "react";
import { PageHeader, EmptyState } from "@enterprise/ui";

export default function EvaluationsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  return (
    <div>
      <PageHeader
        title="Bảng điều khiển Đánh giá & Kiểm định (Eval Dashboard)"
        description="Đo lường độ chính xác câu trả lời, tỷ lệ ảo giác (hallucination rate), mức độ bao phủ trích dẫn nguồn và phân tích độ trễ."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Đánh giá & Kiểm định", isCurrent: true },
        ]}
      />

      <EmptyState
        title="Dữ liệu Kiểm định Đang được Kết nối"
        description="Bảng điều khiển đánh giá theo SPEC-EVAL-001 đang tích hợp với bộ đo lường Ops Metric và sẽ hiển thị khi các bộ dữ liệu kiểm thử hoàn tất chạy thử nghiệm."
      />
    </div>
  );
}
