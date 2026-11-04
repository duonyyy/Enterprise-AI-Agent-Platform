"use client";

import React from "react";
import { PageHeader, EmptyState } from "@enterprise/ui";

export default function ToolsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  return (
    <div>
      <PageHeader
        title="Đăng ký Công cụ (Tool Registry)"
        description="Danh mục các công cụ hệ thống, công cụ MCP và API webhook được cấp quyền cho các Tác tử AI."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Đăng ký Công cụ", isCurrent: true },
        ]}
      />

      <EmptyState
        title="Phân hệ Đăng ký Công cụ đang được chuẩn bị"
        description="Theo lộ trình SPEC-STUDIO-001 (P1), các công cụ sẽ được tự động khám phá từ các Máy chủ MCP đã kết nối hoặc đăng ký qua Tool Gateway với quy trình 12 bước."
        actionLabel="Xem Máy chủ MCP"
        onAction={() => window.location.href = `/w/${workspaceId}/mcp`}
      />
    </div>
  );
}
