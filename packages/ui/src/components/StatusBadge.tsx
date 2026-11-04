import React from "react";

export type StatusType =
  | "PUBLISHED"
  | "DRAFT"
  | "PENDING"
  | "REJECTED"
  | "EXPIRED"
  | "RUNNING"
  | "ARCHIVED"
  | "IMMUTABLE"
  | string;

export interface StatusBadgeProps {
  status: StatusType;
  customLabel?: string;
  size?: "sm" | "md";
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  customLabel,
  size = "md",
}) => {
  const normalized = (status || "").toUpperCase();

  const configs: Record<
    string,
    { label: string; bg: string; color: string; border: string; dot: string }
  > = {
    PUBLISHED: {
      label: "PUBLISHED",
      bg: "var(--color-success-bg)",
      color: "var(--color-success)",
      border: "var(--color-success-border)",
      dot: "var(--color-success)",
    },
    DRAFT: {
      label: "DRAFT",
      bg: "var(--color-warning-bg)",
      color: "var(--color-warning)",
      border: "var(--color-warning-border)",
      dot: "var(--color-warning)",
    },
    PENDING: {
      label: "PENDING",
      bg: "var(--color-warning-bg)",
      color: "var(--color-warning)",
      border: "var(--color-warning-border)",
      dot: "var(--color-warning)",
    },
    REJECTED: {
      label: "REJECTED",
      bg: "var(--color-danger-bg)",
      color: "var(--color-danger)",
      border: "var(--color-danger-border)",
      dot: "var(--color-danger)",
    },
    EXPIRED: {
      label: "EXPIRED",
      bg: "var(--bg-subtle)",
      color: "var(--text-muted)",
      border: "var(--border-subtle)",
      dot: "var(--text-muted)",
    },
    RUNNING: {
      label: "RUNNING",
      bg: "var(--color-info-bg)",
      color: "var(--color-info)",
      border: "var(--color-info-border)",
      dot: "var(--color-info)",
    },
    ARCHIVED: {
      label: "ARCHIVED",
      bg: "var(--bg-subtle)",
      color: "var(--text-muted)",
      border: "var(--border-subtle)",
      dot: "var(--text-muted)",
    },
    IMMUTABLE: {
      label: "IMMUTABLE",
      bg: "var(--color-copper-subtle)",
      color: "var(--color-copper)",
      border: "var(--color-copper-border)",
      dot: "var(--color-copper)",
    },
  };

  const current = configs[normalized] || {
    label: normalized,
    bg: "rgba(100, 116, 139, 0.15)",
    color: "#94A3B8",
    border: "rgba(100, 116, 139, 0.3)",
    dot: "#94A3B8",
  };

  const displayText = customLabel || current.label;

  const isSmall = size === "sm";

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: isSmall ? "4px" : "6px",
        padding: isSmall ? "1px 6px" : "2px 8px",
        fontSize: isSmall ? "10px" : "11px",
        fontWeight: 600,
        letterSpacing: "0.03em",
        borderRadius: "var(--radius-pill)",
        backgroundColor: current.bg,
        color: current.color,
        border: `1px solid ${current.border}`,
        userSelect: "none",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: isSmall ? "4px" : "6px",
          height: isSmall ? "4px" : "6px",
          borderRadius: "50%",
          backgroundColor: current.dot,
          flexShrink: 0,
        }}
        aria-hidden="true"
      />
      <span>{displayText}</span>
    </span>
  );
};

