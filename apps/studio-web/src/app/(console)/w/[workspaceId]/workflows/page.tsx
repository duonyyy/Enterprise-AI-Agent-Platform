"use client";

import React from "react";
import { PageHeader, EmptyState } from "@enterprise/ui";

export default function WorkflowsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  return (
    <div>
      <PageHeader
        title="Trình dựng Quy trình (Workflow Canvas)"
        description="Soạn thảo đồ thị quy trình làm việc đa tác tử, điều phối trạng thái bền bỉ bằng LangGraph và xác lập các chốt kiểm duyệt người dùng."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Quy trình (Workflows)", isCurrent: true },
        ]}
      />

      <EmptyState
        title="Trình dựng Quy trình đang được chuẩn bị"
        description="Khung vẽ đồ thị trực quan sẽ kết nối trực tiếp với đặc tả WorkflowSpec chuẩn tắc theo SPEC-WORKFLOW-001 trong các đợt phát hành tiếp theo."
      />
    </div>
  );
}
