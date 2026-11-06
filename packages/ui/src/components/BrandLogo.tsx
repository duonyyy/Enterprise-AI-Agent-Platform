import React from "react";

export interface BrandLogoProps {
  surface?: "light" | "dark";
  compact?: boolean;
  title?: string;
  subtitle?: string;
  href?: string;
  className?: string;
  style?: React.CSSProperties;
}

export const BrandLogo: React.FC<BrandLogoProps> = ({
  surface = "dark",
  compact = false,
  title = "Agent Studio",
  subtitle = "Enterprise Edition",
  href,
  className = "",
  style,
}) => {
  const isDark = surface === "dark";
  const mainFill = isDark ? "#42B4A7" : "#0F766E";
  const copperFill = "#C2410C";

  const content = (
    <div
      className={`brand-logo ${className}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "10px",
        textDecoration: "none",
        ...style,
      }}
    >
      {/* Monogram Symbol Container */}
      <div
        style={{
          width: compact ? "32px" : "30px",
          height: compact ? "32px" : "30px",
          borderRadius: "6px",
          backgroundColor: isDark ? "rgba(255, 255, 255, 0.05)" : "var(--color-primary-subtle)",
          border: isDark ? "1px solid rgba(255, 255, 255, 0.1)" : "1px solid var(--color-primary-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          boxShadow: isDark
            ? "0 1px 3px rgba(0, 0, 0, 0.3)"
            : "0 1px 3px rgba(15, 118, 110, 0.15)",
        }}
        title="Enterprise Agent Platform"
      >
        <svg
          width={compact ? "24" : "22"}
          height={compact ? "15" : "14"}
          viewBox="90 100 820 520"
          role="img"
          aria-label="Enterprise Agent Platform Portal Monogram"
          style={{ display: "block" }}
        >
          <path
            d="M110 603 C101 603 101 597 107 581 L158 482 L362 482 L395 410 L196 410 L252 297 L482 297 L520 223 L291 223 L333 140 C339 125 348 115 366 115 L636 115 C654 115 664 121 673 139 L890 581 C901 602 889 607 877 607 L664 607 L643 417 L548 365 L459 417 L437 607 L125 607 C116 607 112 606 110 603 Z"
            fill={mainFill}
          />
          <path d="M493 607 L493 439 L548 409 L602 439 L602 607 Z" fill={copperFill} />
        </svg>
      </div>

      {/* Typography Wordmark */}
      {!compact && (
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
          <span
            style={{
              fontSize: "13px",
              fontWeight: 700,
              color: isDark ? "var(--sidebar-text-primary)" : "var(--text-primary)",
              letterSpacing: "-0.01em",
            }}
          >
            {title}
          </span>
          {subtitle && (
            <span
              style={{
                fontSize: "10px",
                fontWeight: 500,
                color: isDark ? "var(--sidebar-text-muted)" : "var(--text-muted)",
              }}
            >
              {subtitle}
            </span>
          )}
        </div>
      )}
    </div>
  );

  if (href) {
    return (
      <a href={href} style={{ textDecoration: "none", color: "inherit" }}>
        {content}
      </a>
    );
  }

  return content;
};

