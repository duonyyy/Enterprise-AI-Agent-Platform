import React from "react";
import { BrandLogo } from "./BrandLogo";

export interface NavItem {
  id: string;
  label: string;
  href: string;
  icon: React.ReactNode;
  badge?: string | number;
  badgeVariant?: "primary" | "warning" | "neutral";
}

export interface NavGroup {
  id: string;
  title: string;
  items: NavItem[];
}

export interface SidebarProps {
  groups: NavGroup[];
  currentPath: string;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
  isMobileOpen?: boolean;
  onCloseMobile?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  groups,
  currentPath,
  isCollapsed = false,
  onToggleCollapse,
  isMobileOpen = false,
  onCloseMobile,
}) => {
  return (
    <>
      {/* Mobile backdrop */}
      {isMobileOpen && (
        <div
          onClick={onCloseMobile}
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "var(--bg-overlay)",
            zIndex: 35,
          }}
          aria-hidden="true"
        />
      )}

      <aside
        className={`app-sidebar ${isCollapsed ? "collapsed" : ""} ${isMobileOpen ? "mobile-open" : ""}`}
        aria-label="Thanh điều hướng chính"
      >
        {/* Brand Header */}
        <div
          style={{
            height: "52px",
            display: "flex",
            alignItems: "center",
            justifyContent: isCollapsed ? "center" : "space-between",
            padding: isCollapsed ? "0" : "0 16px",
            borderBottom: "1px solid var(--sidebar-border)",
            flexShrink: 0,
          }}
        >
          <BrandLogo surface="dark" compact={isCollapsed} />

          {!isCollapsed && onToggleCollapse && (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-label="Thu gọn thanh bên"
              style={{
                padding: "4px",
                color: "var(--sidebar-text-muted)",
                borderRadius: "var(--radius-xs)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
          )}
        </div>

        {/* Navigation Items */}
        <nav
          style={{
            flex: 1,
            overflowY: "auto",
            padding: isCollapsed ? "12px 6px" : "16px 10px",
            display: "flex",
            flexDirection: "column",
            gap: "20px",
          }}
        >
          {groups.map((group) => (
            <div key={group.id} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              {!isCollapsed && (
                <div
                  style={{
                    padding: "4px 8px 6px 8px",
                    fontSize: "10px",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    color: "var(--sidebar-text-muted)",
                  }}
                >
                  {group.title}
                </div>
              )}

              {group.items.map((item) => {
                const isActive = currentPath === item.href || currentPath.startsWith(`${item.href}/`);
                return (
                  <a
                    key={item.id}
                    href={item.href}
                    title={isCollapsed ? item.label : undefined}
                    aria-current={isActive ? "page" : undefined}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: isCollapsed ? "center" : "space-between",
                      padding: isCollapsed ? "10px 0" : "8px 10px",
                      borderRadius: "var(--radius-sm)",
                      backgroundColor: isActive ? "var(--sidebar-active-bg)" : "transparent",
                      color: isActive ? "var(--sidebar-active-text)" : "var(--sidebar-text-secondary)",
                      fontWeight: isActive ? 600 : 400,
                      fontSize: "13px",
                      textDecoration: "none",
                      position: "relative",
                      transition: "all var(--transition-fast)",
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) e.currentTarget.style.backgroundColor = "var(--sidebar-hover)";
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: isActive ? "var(--sidebar-active-indicator)" : "var(--sidebar-text-muted)",
                          flexShrink: 0,
                        }}
                      >
                        {item.icon}
                      </span>
                      {!isCollapsed && <span>{item.label}</span>}
                    </div>

                    {!isCollapsed && item.badge !== undefined && (
                      <span
                        style={{
                          fontSize: "10px",
                          fontWeight: 600,
                          padding: "1px 6px",
                          borderRadius: "var(--radius-pill)",
                          backgroundColor:
                            item.badgeVariant === "warning"
                              ? "rgba(194, 65, 12, 0.25)"
                              : item.badgeVariant === "primary"
                              ? "rgba(15, 118, 110, 0.35)"
                              : "rgba(255, 255, 255, 0.08)",
                          color:
                            item.badgeVariant === "warning"
                              ? "#FB923C"
                              : item.badgeVariant === "primary"
                              ? "#5EEAD4"
                              : "#9CA3AF",
                        }}
                      >
                        {item.badge}
                      </span>
                    )}

                    {/* Active left indicator bar */}
                    {isActive && (
                      <span
                        style={{
                          position: "absolute",
                          left: 0,
                          top: "6px",
                          bottom: "6px",
                          width: "3px",
                          borderRadius: "0 2px 2px 0",
                          backgroundColor: "var(--sidebar-active-indicator)",
                        }}
                        aria-hidden="true"
                      />
                    )}
                  </a>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer info */}
        <div
          style={{
            padding: isCollapsed ? "12px 6px" : "12px 16px",
            borderTop: "1px solid var(--sidebar-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: isCollapsed ? "center" : "space-between",
            fontSize: "11px",
            color: "var(--sidebar-text-muted)",
          }}
        >
          {isCollapsed ? (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-label="Mở rộng thanh bên"
              style={{
                padding: "6px",
                color: "var(--text-muted)",
                cursor: "pointer",
                borderRadius: "var(--radius-xs)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </button>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  style={{
                    width: "6px",
                    height: "6px",
                    borderRadius: "50%",
                    backgroundColor: "var(--color-success)",
                  }}
                  aria-hidden="true"
                />
                <span>Control API Online</span>
              </div>
              <span style={{ fontFamily: "var(--font-mono)" }}>v2.0.0</span>
            </>
          )}
        </div>
      </aside>
    </>
  );
};

