"use client";

import React from "react";
import Link from "next/link";
import { PageHeader, StatusBadge, Button } from "@enterprise/ui";

export default function OverviewPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  const metrics = [
    {
      title: "Tác tử Hoạt động",
      value: "4",
      subtext: "2 bản công bố, 2 bản nháp",
      badge: "ỔN ĐỊNH",
      status: "PUBLISHED",
      link: `/w/${workspaceId}/agents`,
    },
    {
      title: "Phê duyệt Cần xử lý",
      value: "2",
      subtext: "1 giao dịch nhạy cảm, 1 truy vấn",
      badge: "CẦN DUYỆT",
      status: "PENDING",
      link: `/w/${workspaceId}/approvals`,
    },
    {
      title: "Máy chủ MCP Kết nối",
      value: "3",
      subtext: "PostgreSQL, Fetch, GitHub",
      badge: "SẴN SÀNG",
      status: "PUBLISHED",
      link: `/w/${workspaceId}/mcp`,
    },
    {
      title: "Lần chạy Hôm nay",
      value: "148",
      subtext: "Tỷ lệ hoàn thành 99.3%",
      badge: "ĐANG CHẠY",
      status: "RUNNING",
      link: `/w/${workspaceId}/runs`,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Tổng quan Không gian Làm việc"
        description="Trung tâm điều khiển và giám sát vận hành các tác tử thông minh, máy chủ MCP và luồng phê duyệt người dùng."
        breadcrumbs={[
          { label: "Trang chủ", href: `/w/${workspaceId}/overview` },
          { label: "Tổng quan", isCurrent: true },
        ]}
        actions={
          <Link href={`/w/${workspaceId}/agents`}>
            <Button variant="primary" size="md">
              Vào Danh sách Tác tử AI
            </Button>
          </Link>
        }
      />

      {/* Metrics Row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "16px",
          marginBottom: "28px",
        }}
      >
        {metrics.map((m, idx) => (
          <Link
            key={idx}
            href={m.link}
            style={{
              padding: "18px 20px",
              backgroundColor: "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-lg)",
              display: "flex",
              flexDirection: "column",
              gap: "10px",
              textDecoration: "none",
              transition: "all var(--transition-fast)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--border-strong)";
              e.currentTarget.style.backgroundColor = "var(--bg-subtle)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--border-subtle)";
              e.currentTarget.style.backgroundColor = "var(--bg-surface)";
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: 500 }}>
                {m.title}
              </span>
              <StatusBadge status={m.status} customLabel={m.badge} size="sm" />
            </div>
            <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.03em" }}>
              {m.value}
            </div>
            <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              {m.subtext}
            </div>
          </Link>
        ))}
      </div>

      {/* System Highlights */}
      <div
        style={{
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          backgroundColor: "var(--bg-surface)",
          padding: "20px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "14px" }}>
          <div>
            <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
              Tiến trình Vận hành Trực tiếp
            </h2>
            <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "2px 0 0 0" }}>
              Nhật ký kiểm toán thời gian thực từ Agent Runtime và Tool Dispatcher.
            </p>
          </div>
          <Link href={`/w/${workspaceId}/runs`}>
            <Button variant="ghost" size="sm">
              Xem toàn bộ lần chạy &rarr;
            </Button>
          </Link>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {[
            {
              agent: "Trợ lý Soạn thảo Báo giá Doanh nghiệp",
              action: "Hoàn tất trích xuất dữ liệu giá từ máy chủ PostgreSQL MCP",
              time: "Vừa xong",
              status: "PUBLISHED",
            },
            {
              agent: "Chăm sóc Khách hàng Cấp cao",
              action: "Tạo yêu cầu phê duyệt chuyển khoản bồi hoàn 500,000 VND",
              time: "4 phút trước",
              status: "PENDING",
            },
            {
              agent: "Kiểm toán & Đối chiếu Chứng từ",
              action: "Đã công bố phiên bản bất biến v2 (#8c3a9f)",
              time: "2 giờ trước",
              status: "PUBLISHED",
            },
          ].map((item, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 12px",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "var(--bg-subtle)",
                fontSize: "12px",
                gap: "12px",
                flexWrap: "wrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <StatusBadge status={item.status} size="sm" />
                <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                  {item.agent}
                </span>
                <span style={{ color: "var(--text-secondary)" }}>
                  — {item.action}
                </span>
              </div>
              <span style={{ color: "var(--text-muted)", fontSize: "11px", whiteSpace: "nowrap" }}>
                {item.time}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
