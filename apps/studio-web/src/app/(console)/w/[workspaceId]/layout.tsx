"use client";

import React, { useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell, NavGroup, WorkspaceItem, BrandIcon } from "@enterprise/ui";

export default function WorkspaceConsoleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { workspaceId: string };
}) {
  const currentWorkspaceId = params.workspaceId || "default";
  const pathname = usePathname() || "";
  const router = useRouter();

  const availableWorkspaces: WorkspaceItem[] = useMemo(
    () => [
      { id: "default", name: "Default Workspace", tenantName: "VinAI Enterprise" },
      { id: "ws_prod_01", name: "Production Workspace", tenantName: "VinAI Enterprise" },
      { id: "ws_staging_02", name: "Staging Sandbox", tenantName: "VinAI Enterprise" },
    ],
    []
  );

  const handleSelectWorkspace = (newWorkspaceId: string) => {
    // Replace current workspaceId in path
    const pathParts = pathname.split("/");
    const wIndex = pathParts.indexOf("w");
    if (wIndex !== -1 && pathParts[wIndex + 1]) {
      pathParts[wIndex + 1] = newWorkspaceId;
      router.push(pathParts.join("/"));
    } else {
      router.push(`/w/${newWorkspaceId}/agents`);
    }
  };

  const navGroups: NavGroup[] = useMemo(
    () => [
      {
        id: "workspace",
        title: "Workspace",
        items: [
          {
            id: "overview",
            label: "Overview",
            href: `/w/${currentWorkspaceId}/overview`,
            icon: <BrandIcon name="nav-overview" size={18} />,
          },
        ],
      },
      {
        id: "agents-capabilities",
        title: "Agent Studio",
        items: [
          {
            id: "agents",
            label: "AI Agents",
            href: `/w/${currentWorkspaceId}/agents`,
            icon: <BrandIcon name="nav-agents" size={18} />,
            badge: "Core",
            badgeVariant: "primary",
          },
          {
            id: "skills",
            label: "Skills",
            href: `/w/${currentWorkspaceId}/skills`,
            icon: <BrandIcon name="nav-skills" size={18} />,
          },
          {
            id: "mcp",
            label: "MCP Servers",
            href: `/w/${currentWorkspaceId}/mcp`,
            icon: <BrandIcon name="nav-mcp" size={18} />,
          },
          {
            id: "tools",
            label: "Tool Registry",
            href: `/w/${currentWorkspaceId}/tools`,
            icon: <BrandIcon name="nav-tools" size={18} />,
          },
          {
            id: "knowledge",
            label: "Knowledge Base",
            href: `/w/${currentWorkspaceId}/knowledge`,
            icon: <BrandIcon name="nav-knowledge" size={18} />,
          },
          {
            id: "workflows",
            label: "Workflows",
            href: `/w/${currentWorkspaceId}/workflows`,
            icon: <BrandIcon name="nav-workflows" size={18} />,
          },
        ],
      },
      {
        id: "governance",
        title: "Runtime & Governance",
        items: [
          {
            id: "runs",
            label: "Runs & Traces",
            href: `/w/${currentWorkspaceId}/runs`,
            icon: <BrandIcon name="nav-runs" size={18} />,
          },
          {
            id: "approvals",
            label: "Approval Inbox",
            href: `/w/${currentWorkspaceId}/approvals`,
            icon: <BrandIcon name="nav-approvals" size={18} />,
            badge: 2,
            badgeVariant: "warning",
          },
          {
            id: "evaluations",
            label: "Evaluations",
            href: `/w/${currentWorkspaceId}/evaluations`,
            icon: <BrandIcon name="nav-evaluations" size={18} />,
          },
        ],
      },
      {
        id: "settings-group",
        title: "System",
        items: [
          {
            id: "settings",
            label: "Workspace Settings",
            href: `/w/${currentWorkspaceId}/settings`,
            icon: <BrandIcon name="nav-settings" size={18} />,
          },
        ],
      },
    ],
    [currentWorkspaceId]
  );

  return (
    <AppShell
      navGroups={navGroups}
      currentPath={pathname}
      workspaces={availableWorkspaces}
      currentWorkspaceId={currentWorkspaceId}
      onSelectWorkspace={handleSelectWorkspace}
      tenantName="VinAI Enterprise"
      userRole="Agent Platform Admin"
    >
      {children}
    </AppShell>
  );
}

