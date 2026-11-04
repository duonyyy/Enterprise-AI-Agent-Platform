import React, { useState, useRef, useEffect } from "react";

export interface WorkspaceItem {
  id: string;
  name: string;
  tenantName?: string;
  isCurrent?: boolean;
}

export interface WorkspaceSwitcherProps {
  workspaces: WorkspaceItem[];
  currentWorkspaceId: string;
  onSelectWorkspace: (workspaceId: string) => void;
  tenantName?: string;
}

export const WorkspaceSwitcher: React.FC<WorkspaceSwitcherProps> = ({
  workspaces,
  currentWorkspaceId,
  onSelectWorkspace,
  tenantName = "VinAI Enterprise",
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const currentWorkspace = workspaces.find((w) => w.id === currentWorkspaceId) || {
    id: currentWorkspaceId,
    name: "Default Workspace",
    tenantName,
  };

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener("mousedown", handleOutsideClick);
    }
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [isOpen]);

  return (
    <div ref={containerRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-label="Select workspace"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "6px 10px",
          borderRadius: "var(--radius-sm)",
          backgroundColor: isOpen ? "var(--bg-subtle-hover)" : "var(--bg-surface)",
          border: "1px solid var(--border-subtle)",
          boxShadow: "var(--shadow-sm)",
          color: "var(--text-primary)",
          cursor: "pointer",
          fontSize: "12px",
          fontWeight: 500,
          transition: "all var(--transition-fast)",
        }}
      >
        <span
          style={{
            width: "18px",
            height: "18px",
            borderRadius: "4px",
            backgroundColor: "var(--color-primary-subtle)",
            color: "var(--color-primary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "10px",
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          W
        </span>

        <div style={{ display: "flex", flexDirection: "column", textAlign: "left", lineHeight: 1.2 }}>
          <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{currentWorkspace.name}</span>
          <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>{tenantName}</span>
        </div>

        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          style={{
            color: "var(--text-muted)",
            transform: isOpen ? "rotate(180deg)" : "none",
            transition: "transform var(--transition-fast)",
            marginLeft: "4px",
          }}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {isOpen && (
        <div
          role="listbox"
          aria-label="Available workspaces"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            width: "240px",
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-md)",
            zIndex: 50,
            padding: "4px",
            display: "flex",
            flexDirection: "column",
            gap: "2px",
          }}
        >
          <div
            style={{
              padding: "6px 8px 4px 8px",
              fontSize: "10px",
              fontWeight: 600,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            Available Workspaces
          </div>
          {workspaces.map((w) => {
            const isSelected = w.id === currentWorkspaceId;
            return (
              <div
                key={w.id}
                role="option"
                aria-selected={isSelected}
                tabIndex={0}
                onClick={() => {
                  onSelectWorkspace(w.id);
                  setIsOpen(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectWorkspace(w.id);
                    setIsOpen(false);
                  }
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 10px",
                  borderRadius: "var(--radius-xs)",
                  backgroundColor: isSelected ? "var(--bg-subtle)" : "transparent",
                  color: isSelected ? "var(--text-primary)" : "var(--text-secondary)",
                  cursor: "pointer",
                  fontSize: "12px",
                  transition: "background-color var(--transition-fast)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--bg-subtle-hover)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = isSelected ? "var(--bg-subtle)" : "transparent";
                }}
              >
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <span style={{ fontWeight: isSelected ? 600 : 400 }}>{w.name}</span>
                  <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>ID: {w.id}</span>
                </div>
                {isSelected && (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

