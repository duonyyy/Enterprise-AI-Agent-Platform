"use client";

import React, { useState } from "react";
import { PageHeader, DataTable, StatusBadge, Button, Column } from "@enterprise/ui";

interface McpServer {
  id: string;
  name: string;
  transport: string;
  url: string;
  toolsCount: number;
  status: string;
}

export default function McpPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  const [servers] = useState<McpServer[]>([
    {
      id: "mcp_pg_01",
      name: "PostgreSQL Database Engine",
      transport: "SSE Stream",
      url: "http://localhost:8001/mcp/postgres",
      toolsCount: 6,
      status: "PUBLISHED",
    },
    {
      id: "mcp_fetch_02",
      name: "Safe Web Fetcher & Crawler",
      transport: "Streamable HTTP",
      url: "http://localhost:8001/mcp/fetch",
      toolsCount: 2,
      status: "PUBLISHED",
    },
    {
      id: "mcp_git_03",
      name: "Git Repository Operations",
      transport: "Stdio Subprocess Sandbox",
      url: "internal://mcp/git",
      toolsCount: 11,
      status: "PUBLISHED",
    },
  ]);

  const columns: Column<McpServer>[] = [
    {
      key: "name",
      header: "Máy chủ MCP & Địa chỉ",
      width: "45%",
      render: (item) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{item.name}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-muted)" }}>
            {item.url}
          </span>
        </div>
      ),
    },
    {
      key: "transport",
      header: "Phương thức vận chuyển",
      width: "25%",
      render: (item) => <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{item.transport}</span>,
    },
    {
      key: "toolsCount",
      header: "Công cụ cung cấp",
      width: "15%",
      render: (item) => (
        <span style={{ fontWeight: 600, color: "var(--color-primary)" }}>
          {item.toolsCount} công cụ
        </span>
      ),
    },
    {
      key: "status",
      header: "Trạng thái",
      width: "15%",
      render: (item) => <StatusBadge status={item.status} customLabel="KẾT NỐI" />,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Quản lý Máy chủ MCP (Model Context Protocol)"
        description="Cấu hình các kết nối giao thức MCP từ xa, khám phá các công cụ ngoại vi và cấp quyền tương tác cho Tool Gateway."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Máy chủ MCP", isCurrent: true },
        ]}
        badge={<StatusBadge status="PUBLISHED" customLabel={`${servers.length} MÁY CHỦ`} size="sm" />}
      />

      <DataTable columns={columns} data={servers} keyExtractor={(s) => s.id} />
    </div>
  );
}
