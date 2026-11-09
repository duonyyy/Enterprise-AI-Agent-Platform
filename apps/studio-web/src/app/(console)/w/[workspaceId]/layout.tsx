"use client";

import React, { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell, NavGroup, WorkspaceItem, BrandIcon } from "@enterprise/ui";
import { defaultApiClient, CurrentUserResponse } from "@enterprise/api-client";

export default function WorkspaceConsoleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { workspaceId: string };
}) {
  const currentWorkspaceId = params.workspaceId;
  const pathname = usePathname() || "";
  const router = useRouter();

  const [currentUser, setCurrentUser] = useState<CurrentUserResponse | null>(null);

  useEffect(() => {
    // Bootstrap user identity and workspace memberships (Finding R08)
    defaultApiClient
      .getCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        if (!user.workspaces || user.workspaces.length === 0) {
          // User has no active workspaces, redirect to login landing empty state
          router.push("/login");
          return;
        }

        // If current workspace in URL is not among user's active workspaces, redirect to first active workspace
        const hasAccess = user.workspaces.some((w) => w.workspace_id === currentWorkspaceId);
        if (!hasAccess && user.workspaces[0]) {
          router.push(`/w/${user.workspaces[0].workspace_id}/agents`);
        }
      })
      .catch((err: unknown) => {
        if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 401) {
          router.push("/login?error=session_expired");
        }
      });
  }, [currentWorkspaceId, router]);

  const handleLogout = async () => {
    try {
      await defaultApiClient.logout();
    } finally {
      router.push("/login");
    }
  };

  const availableWorkspaces: WorkspaceItem[] = useMemo(() => {
    if (!currentUser || !currentUser.workspaces) {
      return [];
    }
    return currentUser.workspaces.map((w) => ({
      id: w.workspace_id,
      name: w.workspace_name,
      tenantName: w.tenant_name,
    }));
  }, [currentUser]);

  const currentMembership = useMemo(() => {
    return currentUser?.workspaces?.find((w) => w.workspace_id === currentWorkspaceId);
  }, [currentUser, currentWorkspaceId]);

  const userRole = currentMembership?.role ? `Vai trò: ${currentMembership.role}` : "Thành viên";
  const tenantName = currentMembership?.tenant_name || "VinAI Enterprise";

  const handleSelectWorkspace = (newWorkspaceId: string) => {
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
      tenantName={tenantName}
      userRole={userRole}
      userEmail={currentUser?.email_normalized || currentUser?.subject || "user@enterprise.local"}
      userDisplayName={currentUser?.display_name || undefined}
      onLogout={handleLogout}
    >
      {children}
    </AppShell>
  );
}
