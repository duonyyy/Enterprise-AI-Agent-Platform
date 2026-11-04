"use client";

import React from "react";
import { PageHeader, StatusBadge, Button, Input } from "@enterprise/ui";

export default function SettingsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  return (
    <div style={{ maxWidth: "800px" }}>
      <PageHeader
        title="Cài đặt Không gian Làm việc"
        description="Quản lý định danh workspace, cấu hình ranh giới bảo mật và thông tin phân quyền vai trò người dùng."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Cài đặt", isCurrent: true },
        ]}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
        {/* Section 1: General Info */}
        <div
          style={{
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
            Thông tin Cơ bản
          </h2>

          <Input
            label="Mã định danh Không gian làm việc (Workspace ID)"
            value={workspaceId}
            disabled
            helperText="Được gắn định danh bất biến trong cơ sở dữ liệu."
          />

          <Input
            label="Tên Không gian Làm việc"
            defaultValue="Không gian Mặc định (Production)"
            helperText="Tên gợi nhớ cho các thành viên trong tổ chức."
          />

          <Input
            label="Tổ chức / Tenant Chủ quản"
            value="VinAI Enterprise Platform"
            disabled
          />
        </div>

        {/* Section 2: Security & Zero-Trust */}
        <div
          style={{
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "14px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
              Chính sách Bảo mật & Ranh giới Tin cậy
            </h2>
            <StatusBadge status="IMMUTABLE" customLabel="BẢO VỆ ĐA TẦNG" size="sm" />
          </div>

          <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
            Toàn bộ các khóa bí mật (API Keys, Secret Ref) được lưu trữ tại Vault và không bao giờ gửi về trình duyệt. Mọi hoạt động ghi tác động cao bắt buộc phải có chữ ký phê duyệt CAS (Content-Addressed Storage).
          </p>

          <div
            style={{
              padding: "10px 14px",
              backgroundColor: "var(--bg-subtle)",
              borderRadius: "var(--radius-sm)",
              fontSize: "12px",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-success)" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            <span>Row-Level Security (RLS) đang được kích hoạt cưỡng chế tại máy chủ PostgreSQL 56 bảng.</span>
          </div>
        </div>

        {/* Save button */}
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Button variant="primary" size="md">
            Lưu thay đổi Cài đặt
          </Button>
        </div>
      </div>
    </div>
  );
}
