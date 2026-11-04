"use client";

import React, { useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell, NavGroup, WorkspaceItem } from "@enterprise/ui";

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
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="9" rx="1" />
                <rect x="14" y="3" width="7" height="5" rx="1" />
                <rect x="14" y="12" width="7" height="9" rx="1" />
                <rect x="3" y="16" width="7" height="5" rx="1" />
              </svg>
            ),
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
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="11" width="18" height="10" rx="2" />
                <circle cx="12" cy="5" r="2" />
                <path d="M12 7v4" />
                <line x1="8" y1="16" x2="8" y2="16" />
                <line x1="16" y1="16" x2="16" y2="16" />
              </svg>
            ),
            badge: "Core",
            badgeVariant: "primary",
          },
          {
            id: "skills",
            label: "Skills",
            href: `/w/${currentWorkspaceId}/skills`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
              </svg>
            ),
          },
          {
            id: "mcp",
            label: "MCP Servers",
            href: `/w/${currentWorkspaceId}/mcp`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                <line x1="6" y1="6" x2="6.01" y2="6" />
                <line x1="6" y1="18" x2="6.01" y2="18" />
              </svg>
            ),
          },
          {
            id: "tools",
            label: "Tool Registry",
            href: `/w/${currentWorkspaceId}/tools`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </svg>
            ),
          },
          {
            id: "knowledge",
            label: "Knowledge Base",
            href: `/w/${currentWorkspaceId}/knowledge`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
            ),
          },
          {
            id: "workflows",
            label: "Workflows",
            href: `/w/${currentWorkspaceId}/workflows`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="18" cy="18" r="3" />
                <circle cx="6" cy="6" r="3" />
                <path d="M13 6h3a2 2 0 0 1 2 2v7" />
                <line x1="6" y1="9" x2="6" y2="21" />
              </svg>
            ),
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
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            ),
          },
          {
            id: "approvals",
            label: "Approval Inbox",
            href: `/w/${currentWorkspaceId}/approvals`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
            ),
            badge: 2,
            badgeVariant: "warning",
          },
          {
            id: "evaluations",
            label: "Evaluations",
            href: `/w/${currentWorkspaceId}/evaluations`,
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="20" x2="18" y2="10" />
                <line x1="12" y1="20" x2="12" y2="4" />
                <line x1="6" y1="20" x2="6" y2="14" />
              </svg>
            ),
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
            icon: (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            ),
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

