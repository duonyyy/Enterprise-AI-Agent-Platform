"use client";

import React from "react";
import { PageHeader, EmptyState } from "@enterprise/ui";

export default function KnowledgePage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  return (
    <div>
      <PageHeader
        title="Kho Tri thức (Knowledge Explorer)"
        description="Quản lý tài liệu nguồn, lập chỉ mục vector lai (Hybrid E5 Dense + Sparse BM25) và kiểm soát quyền truy cập tài liệu RAG."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Kho tri thức", isCurrent: true },
        ]}
      />

      <EmptyState
        title="Hợp đồng API Knowledge đang trong lộ trình tích hợp"
        description="Theo đặc tả SPEC-STUDIO-001 / ST-06, giao thức tải tệp lên và kiểm dịch mã độc (Ingestion Quarantine) đang được triển khai trên backend. Giao diện từ chối tạo dữ liệu giả trước khi API sẵn sàng."
      />
    </div>
  );
}
