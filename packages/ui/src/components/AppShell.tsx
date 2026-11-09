import React, { useState } from "react";
import { Sidebar, NavGroup } from "./Sidebar";
import { WorkspaceSwitcher, WorkspaceItem } from "./WorkspaceSwitcher";

export interface AppShellProps {
  children: React.ReactNode;
  navGroups: NavGroup[];
  currentPath: string;
  workspaces: WorkspaceItem[];
  currentWorkspaceId: string;
  onSelectWorkspace: (workspaceId: string) => void;
  tenantName?: string;
  userRole?: string;
  userEmail?: string;
  userDisplayName?: string;
  onLogout?: () => void;
}

export const AppShell: React.FC<AppShellProps> = ({
  children,
  navGroups,
  currentPath,
  workspaces,
  currentWorkspaceId,
  onSelectWorkspace,
  tenantName = "VinAI Enterprise",
  userRole = "Chuyên viên Quản trị",
  userEmail = "admin@vinai.io",
  userDisplayName,
  onLogout,
}) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <Sidebar
        groups={navGroups}
        currentPath={currentPath}
        isCollapsed={isCollapsed}
        onToggleCollapse={() => setIsCollapsed(!isCollapsed)}
        isMobileOpen={isMobileOpen}
        onCloseMobile={() => setIsMobileOpen(false)}
      />

      {/* Main Container */}
      <div className="app-main">
        {/* Topbar */}
        <header className="app-topbar">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {/* Mobile hamburger menu toggle */}
            <button
              type="button"
              onClick={() => setIsMobileOpen(!isMobileOpen)}
              aria-label="Mở danh mục điều hướng"
              style={{
                display: "none",
                padding: "6px",
                color: "var(--text-secondary)",
                cursor: "pointer",
              }}
              className="mobile-menu-btn"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="3" y1="12" x2="21" y2="12" />
                <line x1="3" y1="6" x2="21" y2="6" />
                <line x1="3" y1="18" x2="21" y2="18" />
              </svg>
            </button>

            {/* Workspace Switcher in Topbar */}
            <WorkspaceSwitcher
              workspaces={workspaces}
              currentWorkspaceId={currentWorkspaceId}
              onSelectWorkspace={onSelectWorkspace}
              tenantName={tenantName}
            />
          </div>

          {/* Right Topbar Actions */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {/* Free SaaS Badge - Copper Accent */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                padding: "2px 8px",
                borderRadius: "var(--radius-xs)",
                backgroundColor: "var(--color-copper-subtle)",
                border: "1px solid var(--color-copper-border)",
                fontSize: "11px",
                fontWeight: 600,
                color: "var(--color-copper)",
              }}
            >
              Free SaaS
            </div>

            {/* User profile / role badge */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                paddingLeft: "12px",
                borderLeft: "1px solid var(--border-subtle)",
              }}
            >
              <div
                style={{
                  width: "28px",
                  height: "28px",
                  borderRadius: "50%",
                  backgroundColor: "var(--color-primary-subtle)",
                  border: "1px solid var(--color-primary-border)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "var(--color-primary)",
                }}
              >
                {(userDisplayName || userEmail || "U").substring(0, 2).toUpperCase()}
              </div>
              <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
                <span style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-primary)" }}>
                  {userDisplayName || userEmail}
                </span>
                <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>
                  {userRole}
                </span>
              </div>
              {onLogout && (
                <button
                  type="button"
                  onClick={onLogout}
                  title="Đăng xuất"
                  style={{
                    marginLeft: "8px",
                    padding: "4px 8px",
                    borderRadius: "var(--radius-xs)",
                    border: "1px solid var(--border-subtle)",
                    backgroundColor: "transparent",
                    color: "var(--text-secondary)",
                    fontSize: "11px",
                    cursor: "pointer",
                  }}
                >
                  Đăng xuất
                </button>
              )}
            </div>
          </div>
        </header>

        {/* Page Content */}
        <main className="app-content" id="main-content">
          {children}
        </main>
      </div>

      <style>{`
        @media (max-width: 768px) {
          .mobile-menu-btn {
            display: flex !important;
          }
        }
      `}</style>
    </div>
  );
};

