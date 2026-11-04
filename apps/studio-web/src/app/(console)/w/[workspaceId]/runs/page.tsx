"use client";

import React, { useState } from "react";
import { PageHeader, DataTable, StatusBadge, Button, Column } from "@enterprise/ui";

interface RunItem {
  id: string;
  agentName: string;
  version: string;
  status: string;
  latencyMs: number;
  stepsCount: number;
  startedAt: string;
}

export default function RunsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  const [runs] = useState<RunItem[]>([
    {
      id: "run_9a8b7c6d",
      agentName: "Trợ lý Soạn thảo Báo giá Doanh nghiệp",
      version: "v3",
      status: "PUBLISHED",
      latencyMs: 1420,
      stepsCount: 4,
      startedAt: new Date(Date.now() - 300000).toISOString(),
    },
    {
      id: "run_8f3a1b2c",
      agentName: "Chăm sóc Khách hàng Cấp cao",
      version: "v1-draft",
      status: "RUNNING",
      latencyMs: 850,
      stepsCount: 2,
      startedAt: new Date(Date.now() - 60000).toISOString(),
    },
    {
      id: "run_7c2e8a1f",
      agentName: "Kiểm toán & Đối chiếu Chứng từ",
      version: "v2",
      status: "PUBLISHED",
      latencyMs: 3200,
      stepsCount: 7,
      startedAt: new Date(Date.now() - 3600000).toISOString(),
    },
  ]);

  const columns: Column<RunItem>[] = [
    {
      key: "id",
      header: "Mã lần chạy & Tác tử",
      width: "40%",
      render: (item) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{item.agentName}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-muted)" }}>
            ID: {item.id} · Phiên bản: {item.version}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Trạng thái chạy",
      width: "18%",
      render: (item) => (
        <StatusBadge
          status={item.status}
          customLabel={item.status === "RUNNING" ? "ĐANG CHẠY" : "HOÀN TẤT"}
        />
      ),
    },
    {
      key: "stepsCount",
      header: "Số bước",
      width: "14%",
      render: (item) => <span>{item.stepsCount} bước</span>,
    },
    {
      key: "latencyMs",
      header: "Thời gian xử lý",
      width: "14%",
      render: (item) => (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-secondary)" }}>
          {item.latencyMs} ms
        </span>
      ),
    },
    {
      key: "actions",
      header: "Thao tác",
      width: "14%",
      align: "right",
      render: (item) => (
        <Button variant="ghost" size="sm">
          Thanh tra SSE
        </Button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Lịch sử Chạy & Thanh tra Lần chạy (Run Inspector)"
        description="Giám sát luồng sự kiện SSE, thời gian phản hồi, trạng thái thực thi công cụ và kiểm toán bước chạy theo thời gian thực."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Lịch sử Chạy", isCurrent: true },
        ]}
        badge={<StatusBadge status="RUNNING" customLabel={`${runs.length} LẦN CHẠY`} size="sm" />}
      />

      <DataTable columns={columns} data={runs} keyExtractor={(r) => r.id} />
    </div>
  );
}
