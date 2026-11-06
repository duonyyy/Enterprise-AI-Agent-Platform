import React from "react";

export type NavIconName =
  | "nav-overview"
  | "nav-agents"
  | "nav-skills"
  | "nav-mcp"
  | "nav-tools"
  | "nav-knowledge"
  | "nav-workflows"
  | "nav-runs"
  | "nav-approvals"
  | "nav-evaluations"
  | "nav-settings"
  | "nav-workspace"
  | "nav-business";

export type StatusActionIconName =
  | "action-approve"
  | "action-compare"
  | "action-publish"
  | "action-reject"
  | "action-rollback"
  | "run-cancelled"
  | "run-failed"
  | "run-running"
  | "run-succeeded"
  | "run-waiting-approval"
  | "safety-quarantine"
  | "safety-shield"
  | "status-archived"
  | "status-draft"
  | "status-immutable"
  | "status-published";

export type BrandIconName = NavIconName | StatusActionIconName;

export interface BrandIconProps {
  name: BrandIconName;
  size?: number;
  className?: string;
  color?: string;
  style?: React.CSSProperties;
  ariaLabel?: string;
}

export const BrandIcon: React.FC<BrandIconProps> = ({
  name,
  size = 18,
  className = "",
  color = "currentColor",
  style,
  ariaLabel,
}) => {
  const renderPaths = () => {
    switch (name) {
      // Navigation Icons
      case "nav-agents":
        return (
          <>
            <path d="M8 6h8l4 6-4 6H8L4 12z" />
            <path d="M12 9v6" />
            <path d="M9.5 12H14.5" />
            <path d="M10 18v-3.5c0-1.1.9-2 2-2s2 .9 2 2V18" />
          </>
        );
      case "nav-approvals":
        return (
          <>
            <path d="M5 7.5h14v9a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
            <path d="M5 8l7 5 7-5" />
            <path d="M15.5 6l1.5 1.5 3-3" />
          </>
        );
      case "nav-business":
        return (
          <>
            <rect x="5" y="9" width="14" height="10" rx="1.5" />
            <path d="M9 9V6.5A1.5 1.5 0 0 1 10.5 5h3A1.5 1.5 0 0 1 15 6.5V9" />
            <path d="M5 13h14" />
          </>
        );
      case "nav-evaluations":
        return (
          <>
            <path d="M6 18.5V10" />
            <path d="M11 18.5V7" />
            <path d="M16 18.5V12" />
            <path d="M4.5 18.5h13" />
            <path d="M16.5 6.5l1.5 1.5 3-3" />
          </>
        );
      case "nav-knowledge":
        return (
          <>
            <path d="M7 5.5h10v10H7z" />
            <path d="M10 5.5v-1.5h7v10H15" />
            <path d="M9.5 9h5M9.5 12h5M9.5 15h3.5" />
          </>
        );
      case "nav-mcp":
        return (
          <>
            <rect x="8" y="8" width="8" height="8" rx="2" />
            <path d="M12 3.5V8M12 16v4.5M3.5 12H8M16 12h4.5M6 6l2.5 2.5M18 6l-2.5 2.5M6 18l2.5-2.5M18 18l-2.5-2.5" />
          </>
        );
      case "nav-overview":
        return (
          <>
            <rect x="4" y="4" width="6" height="6" rx="1" />
            <rect x="13" y="4" width="7" height="4" rx="1" />
            <rect x="4" y="13" width="4" height="7" rx="1" />
            <rect x="11" y="11" width="9" height="9" rx="1.5" />
          </>
        );
      case "nav-runs":
        return (
          <>
            <path d="M5 18h14" />
            <path d="M7 16V8" />
            <path d="M12 16V5" />
            <path d="M17 16v-6" />
            <path d="M5 5l3 3 3-2 3 2 5-3" />
          </>
        );
      case "nav-settings":
        return (
          <>
            <path d="M5 7h10" />
            <circle cx="9" cy="7" r="2" />
            <path d="M9 17h10" />
            <circle cx="15" cy="17" r="2" />
            <path d="M5 12h14" />
            <circle cx="13" cy="12" r="2" />
          </>
        );
      case "nav-skills":
        return (
          <>
            <path d="M6 5.5h9a2 2 0 0 1 2 2V19H8a2 2 0 0 0-2-2z" />
            <path d="M6 5.5v11.5a2 2 0 0 1 2 2V7.5a2 2 0 0 0-2-2z" />
            <path d="M15 5.5v4l-2-1.2-2 1.2v-4" />
          </>
        );
      case "nav-tools":
        return (
          <>
            <path d="M14 4.5a4 4 0 0 0-4.8 4.9l-4.7 4.7a1.5 1.5 0 0 0 0 2.1l1.3 1.3a1.5 1.5 0 0 0 2.1 0l4.7-4.7A4 4 0 1 0 14 4.5z" />
            <path d="M15.5 8.5l-1 1" />
            <rect x="15.75" y="14.75" width="4.5" height="4.5" rx="1" />
          </>
        );
      case "nav-workflows":
        return (
          <>
            <circle cx="6" cy="7" r="2" />
            <circle cx="18" cy="7" r="2" />
            <circle cx="12" cy="17" r="2" />
            <path d="M8 7h8M7.5 8.5l3 6M16.5 8.5l-3 6" />
          </>
        );
      case "nav-workspace":
        return (
          <>
            <path d="M4.5 8l7.5-4 7.5 4-7.5 4z" />
            <path d="M4.5 16l7.5-4 7.5 4-7.5 4z" />
            <path d="M12 8v4" />
          </>
        );

      // Status & Action Icons
      case "action-approve":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M9 12.5l2 2 4-5" />
          </>
        );
      case "action-compare":
        return (
          <>
            <rect x="5" y="7" width="6" height="10" rx="1" />
            <rect x="13" y="7" width="6" height="10" rx="1" />
            <path d="M11 12h2" />
          </>
        );
      case "action-publish":
        return (
          <>
            <path d="M12 16V6" />
            <path d="M8.5 9.5L12 6l3.5 3.5" />
            <path d="M6 18.5h12" />
          </>
        );
      case "action-reject":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M9.5 9.5l5 5M14.5 9.5l-5 5" />
          </>
        );
      case "action-rollback":
        return (
          <>
            <path d="M9 8.5L5.5 12 9 15.5" />
            <path d="M6.5 7.5H13a4.5 4.5 0 1 1 0 9" />
          </>
        );
      case "run-cancelled":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M8.5 12h7" />
          </>
        );
      case "run-failed":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M9.5 9.5l5 5M14.5 9.5l-5 5" />
          </>
        );
      case "run-running":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M12 8.5V12l2.5 2.5" />
          </>
        );
      case "run-succeeded":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M9 12.5l2 2 4-5" />
          </>
        );
      case "run-waiting-approval":
        return (
          <>
            <circle cx="12" cy="12" r="6.5" />
            <path d="M12 8.5v4" />
            <circle cx="12" cy="15.5" r="0.5" fill="currentColor" stroke="none" />
          </>
        );
      case "safety-quarantine":
        return (
          <>
            <path d="M7.5 5.5h9l3 3v7l-3 3h-9l-3-3v-7z" />
            <path d="M9.5 9.5l5 5M14.5 9.5l-5 5" />
          </>
        );
      case "safety-shield":
        return (
          <>
            <path d="M12 3.5l6.5 3v5c0 4.2-2.7 6.9-6.5 9-3.8-2.1-6.5-4.8-6.5-9v-5z" />
            <path d="M9.5 12.5l1.5 1.5 3.5-4" />
          </>
        );
      case "status-archived":
        return (
          <>
            <path d="M5.5 7h13l-1 11a1.5 1.5 0 0 1-1.5 1.5h-8a1.5 1.5 0 0 1-1.5-1.5z" />
            <path d="M4.5 4.5h15v3h-15z" />
            <path d="M10 12h4" />
          </>
        );
      case "status-draft":
        return (
          <>
            <path d="M7 4.5h7l3 3V19H7z" />
            <path d="M14 4.5V8h3" />
            <path d="M9.5 12h5M9.5 15h3" />
          </>
        );
      case "status-immutable":
        return (
          <>
            <path d="M12 3.5l6.5 3v5c0 4.2-2.7 6.9-6.5 9-3.8-2.1-6.5-4.8-6.5-9v-5z" />
            <rect x="9" y="10.5" width="6" height="5" rx="1" />
            <path d="M10.5 10.5V9a1.5 1.5 0 0 1 3 0v1.5" />
          </>
        );
      case "status-published":
        return (
          <>
            <circle cx="12" cy="12" r="7" />
            <path d="M9 12.5l2 2 4-5" />
          </>
        );
      default:
        return null;
    }
  };

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flexShrink: 0, ...style }}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      role={ariaLabel ? "img" : undefined}
    >
      {renderPaths()}
    </svg>
  );
};

