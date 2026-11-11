"use client";

import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppShell, NavGroup, WorkspaceItem, BrandIcon, Button } from "@enterprise/ui";
import { defaultApiClient, CurrentUserResponse, ApiClientError } from "@enterprise/api-client";

type LayoutStatus = "loading" | "authorized" | "service_error";

function broadcastLogout() {
  try {
    if (typeof window !== "undefined" && "BroadcastChannel" in window) {
      const channel = new BroadcastChannel("enterprise_auth_channel");
      channel.postMessage("LOGOUT");
      channel.close();
    }
    localStorage.setItem("enterprise_auth_logout", Date.now().toString());
  } catch {
    // Ignore channel/storage errors
  }
}

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
  const [layoutStatus, setLayoutStatus] = useState<LayoutStatus>("loading");
  const isRefreshingRef = useRef(false);

  // Authentication & Membership Bootstrap Guard (D4-A / Finding R08)
  const bootstrapAuth = useCallback(async () => {
    setLayoutStatus("loading");
    try {
      const user = await defaultApiClient.getCurrentUser();
      if (!user.workspaces || user.workspaces.length === 0) {
        // User has no active workspaces, redirect to login landing empty state
        router.replace("/login");
        return;
      }

      // Check if current workspace in URL is among user's active workspaces
      const hasAccess = user.workspaces.some((w) => w.workspace_id === currentWorkspaceId);
      if (!hasAccess) {
        const fallbackWid = user.workspaces[0]?.workspace_id;
        if (fallbackWid) {
          router.replace(`/w/${fallbackWid}/agents`);
        } else {
          router.replace("/login");
        }
        return;
      }

      setCurrentUser(user);
      setLayoutStatus("authorized");
    } catch (err: unknown) {
      if (err instanceof ApiClientError && err.status === 401) {
        // Single attempt to refresh session before redirecting to login
        try {
          await defaultApiClient.refreshSession();
          const refreshedUser = await defaultApiClient.getCurrentUser();
          const hasAccess = refreshedUser.workspaces?.some((w) => w.workspace_id === currentWorkspaceId);
          if (hasAccess) {
            setCurrentUser(refreshedUser);
            setLayoutStatus("authorized");
            return;
          }
          if (refreshedUser.workspaces && refreshedUser.workspaces.length > 0) {
            router.replace(`/w/${refreshedUser.workspaces[0]?.workspace_id}/agents`);
            return;
          }
          router.replace("/login");
        } catch {
          router.replace(`/login?error=session_expired&return_to=${encodeURIComponent(pathname)}`);
        }
      } else {
        // Infrastructure failure (503 / Network failure) - Fail closed, never render fake data
        setLayoutStatus("service_error");
      }
    }
  }, [currentWorkspaceId, pathname, router]);

  useEffect(() => {
    bootstrapAuth();
  }, [bootstrapAuth]);

  // Cross-tab logout synchronization (Finding R08 / D4-C)
  useEffect(() => {
    let authChannel: BroadcastChannel | null = null;
    try {
      if (typeof window !== "undefined" && "BroadcastChannel" in window) {
        authChannel = new BroadcastChannel("enterprise_auth_channel");
        authChannel.onmessage = (event) => {
          if (event.data === "LOGOUT") {
            setCurrentUser(null);
            router.replace("/login?logged_out=1");
          }
        };
      }
    } catch {
      // Ignore
    }

    const handleStorage = (e: StorageEvent) => {
      if (e.key === "enterprise_auth_logout" && e.newValue) {
        setCurrentUser(null);
        router.replace("/login?logged_out=1");
      }
    };
    window.addEventListener("storage", handleStorage);

    return () => {
      authChannel?.close();
      window.removeEventListener("storage", handleStorage);
    };
  }, [router]);

  // Periodic session refresh & visibility-change listener (D4-B)
  useEffect(() => {
    if (layoutStatus !== "authorized") return;

    const performRefresh = async () => {
      if (isRefreshingRef.current) return;
      isRefreshingRef.current = true;
      try {
        await defaultApiClient.refreshSession();
      } catch (err: unknown) {
        if (err instanceof ApiClientError && err.status === 401) {
          setCurrentUser(null);
          router.replace(`/login?error=session_expired&return_to=${encodeURIComponent(pathname)}`);
        }
      } finally {
        isRefreshingRef.current = false;
      }
    };

    // Refresh every 10 minutes while active tab
    const intervalId = setInterval(performRefresh, 10 * 60 * 1000);

    // Refresh on tab refocus if needed
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        performRefresh();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [layoutStatus, pathname, router]);

  // Logout handler with 503 distinction (Finding C03 / D4-C)
  const handleLogout = async () => {
    broadcastLogout();
    setCurrentUser(null);
    try {
      await defaultApiClient.logout();
      router.replace("/login?logged_out=1");
    } catch (err: unknown) {
      if (err instanceof ApiClientError && err.status === 503) {
        router.replace("/login?error=logout_degraded");
      } else {
        router.replace("/login?error=auth_failed");
      }
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

  // 1. Loading State Screen (Never render children or console shell while verifying)
  if (layoutStatus === "loading") {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "var(--bg-canvas)",
          gap: "16px",
        }}
      >
        <div
          style={{
            width: "48px",
            height: "48px",
            borderRadius: "12px",
            backgroundColor: "var(--color-primary-subtle)",
            border: "1px solid var(--color-primary-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            animation: "pulse 1.5s infinite ease-in-out",
          }}
        >
          <BrandIcon name="safety-shield" size={24} color="var(--color-primary)" />
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)" }}>
            Đang xác thực phiên làm việc...
          </div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
            Kiểm tra chứng thực và quyền truy cập Workspace
          </div>
        </div>
      </div>
    );
  }

  // 2. Service Error Screen (Fail-Closed: 503 / Backend Unreachable)
  if (layoutStatus === "service_error") {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "var(--bg-canvas)",
          padding: "24px",
        }}
      >
        <div
          style={{
            maxWidth: "460px",
            width: "100%",
            backgroundColor: "var(--bg-surface)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            borderRadius: "var(--radius-lg)",
            padding: "32px",
            boxShadow: "var(--shadow-lg)",
            display: "flex",
            flexDirection: "column",
            gap: "20px",
            textAlign: "center",
          }}
        >
          <div
            style={{
              width: "48px",
              height: "48px",
              borderRadius: "50%",
              backgroundColor: "rgba(239, 68, 68, 0.1)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto",
            }}
          >
            <BrandIcon name="safety-shield" size={24} color="#dc2626" />
          </div>
          <div>
            <h2 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
              Dịch vụ xác thực gián đoạn (503)
            </h2>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", marginTop: "8px", lineHeight: 1.5 }}>
              Không thể kết nối hoặc xác thực phiên làm việc với máy chủ Control API. Dữ liệu chưa thể tải an toàn.
            </p>
          </div>
          <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
            <Button variant="primary" size="md" onClick={() => bootstrapAuth()}>
              Thử lại
            </Button>
            <Button variant="secondary" size="md" onClick={() => router.replace("/login")}>
              Quay về trang đăng nhập
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // 3. Authorized State Screen: Render Full Console Shell & Business Children
  return (
    <AppShell
      navGroups={navGroups}
      currentPath={pathname}
      workspaces={availableWorkspaces}
      currentWorkspaceId={currentWorkspaceId}
      onSelectWorkspace={handleSelectWorkspace}
      tenantName={tenantName}
      userRole={userRole}
      userEmail={currentUser?.email_normalized || currentUser?.subject || ""}
      userDisplayName={currentUser?.display_name || undefined}
      onLogout={handleLogout}
    >
      {children}
    </AppShell>
  );
}
