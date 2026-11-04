"use client";

import React, { useState } from "react";
import { PageHeader, DataTable, StatusBadge, Button, Column } from "@enterprise/ui";

interface SkillItem {
  id: string;
  name: string;
  slug: string;
  version: string;
  author: string;
  lifecycle: string;
  description: string;
}

export default function SkillsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  const [skills] = useState<SkillItem[]>([
    {
      id: "sk_01",
      name: "Quy chuẩn Kiểm thử Thuộc tính (Property-Based Testing)",
      slug: "property-based-testing",
      version: "v1.2.0",
      author: "Platform Team",
      lifecycle: "PUBLISHED",
      description: "Tập hợp các bộ sinh giả định và kiểm chứng bất biến logic trên miền giá trị toàn phần.",
    },
    {
      id: "sk_02",
      name: "Tối ưu hóa Truy vấn Cơ sở dữ liệu Postgres",
      slug: "supabase-postgres-best-practices",
      version: "v2.0.1",
      author: "Database Team",
      lifecycle: "PUBLISHED",
      description: "Quy chuẩn đánh chỉ mục, kiểm tra kế hoạch thực thi EXPLAIN và kiểm định chính sách bảo vệ RLS.",
    },
    {
      id: "sk_03",
      name: "Kiểm tra Tuân thủ Đặc tả Kỹ thuật (Spec-to-Code)",
      slug: "spec-to-code-compliance",
      version: "v1.0.0",
      author: "Governance Lead",
      lifecycle: "PUBLISHED",
      description: "So sánh mã nguồn thực tế đối chiếu với các tài liệu đặc tả miền nghiệp vụ để phát hiện sai lệch.",
    },
  ]);

  const columns: Column<SkillItem>[] = [
    {
      key: "name",
      header: "Kỹ năng (Skill) & Mô tả",
      width: "45%",
      render: (item) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{item.name}</span>
          <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{item.description}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-muted)" }}>
            Slug: {item.slug}
          </span>
        </div>
      ),
    },
    {
      key: "version",
      header: "Phiên bản",
      width: "15%",
      render: (item) => (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-primary)" }}>
          {item.version}
        </span>
      ),
    },
    {
      key: "author",
      header: "Tác giả",
      width: "20%",
      render: (item) => <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{item.author}</span>,
    },
    {
      key: "lifecycle",
      header: "Trạng thái",
      width: "20%",
      render: (item) => <StatusBadge status={item.lifecycle} />,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Quản lý Kỹ năng (Skill Management)"
        description="Quản lý các gói chỉ lệnh chuyên biệt, bộ tài liệu SKILL.md và phiên bản bất biến gắn kết vào Tác tử AI."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Kỹ năng (Skills)", isCurrent: true },
        ]}
        badge={<StatusBadge status="PUBLISHED" customLabel={`${skills.length} KỸ NĂNG`} size="sm" />}
      />

      <DataTable columns={columns} data={skills} keyExtractor={(s) => s.id} />
    </div>
  );
}
