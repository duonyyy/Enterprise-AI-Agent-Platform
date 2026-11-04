import React from "react";

export interface BreadcrumbItem {
  label: string;
  href?: string;
  isCurrent?: boolean;
}

export interface BreadcrumbProps {
  items: BreadcrumbItem[];
}

export const Breadcrumb: React.FC<BreadcrumbProps> = ({ items }) => {
  return (
    <nav aria-label="Đường dẫn điều hướng" style={{ marginBottom: "8px" }}>
      <ol
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          listStyle: "none",
          padding: 0,
          margin: 0,
          fontSize: "12px",
          color: "var(--text-muted)",
        }}
      >
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <li key={index} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              {index > 0 && (
                <span style={{ color: "var(--border-strong)", userSelect: "none" }} aria-hidden="true">
                  /
                </span>
              )}
              {isLast || !item.href ? (
                <span
                  style={{
                    color: isLast ? "var(--text-secondary)" : "var(--text-muted)",
                    fontWeight: isLast ? 500 : 400,
                  }}
                  aria-current={isLast ? "page" : undefined}
                >
                  {item.label}
                </span>
              ) : (
                <a
                  href={item.href}
                  style={{
                    color: "var(--text-muted)",
                    textDecoration: "none",
                    transition: "color var(--transition-fast)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = "var(--text-primary)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = "var(--text-muted)";
                  }}
                >
                  {item.label}
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

