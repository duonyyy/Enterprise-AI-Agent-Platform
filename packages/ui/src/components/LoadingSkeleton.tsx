import React from "react";

export interface LoadingSkeletonProps {
  rows?: number;
  columns?: number;
  label?: string;
}

export const LoadingSkeleton: React.FC<LoadingSkeletonProps> = ({
  rows = 5,
  columns = 4,
  label = "Đang tải dữ liệu...",
}) => {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      style={{
        width: "100%",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--bg-surface)",
        overflow: "hidden",
      }}
    >
      <span className="sr-only" style={{ display: "none" }}>
        {label}
      </span>
      {/* Table Header Skeleton */}
      <div
        style={{
          display: "flex",
          padding: "12px 14px",
          backgroundColor: "var(--bg-subtle)",
          borderBottom: "1px solid var(--border-subtle)",
          gap: "16px",
        }}
      >
        {Array.from({ length: columns }).map((_, idx) => (
          <div
            key={idx}
            className="skeleton-shimmer"
            style={{
              height: "12px",
              width: idx === 0 ? "160px" : idx === columns - 1 ? "80px" : "120px",
            }}
          />
        ))}
      </div>

      {/* Table Rows Skeleton */}
      {Array.from({ length: rows }).map((_, rIdx) => (
        <div
          key={rIdx}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "14px",
            borderBottom: rIdx === rows - 1 ? "none" : "1px solid var(--border-subtle)",
            gap: "16px",
          }}
        >
          {Array.from({ length: columns }).map((_, cIdx) => (
            <div
              key={cIdx}
              className="skeleton-shimmer"
              style={{
                height: "14px",
                width:
                  cIdx === 0
                    ? `${140 + ((rIdx * 17) % 60)}px`
                    : cIdx === columns - 1
                    ? "70px"
                    : `${90 + ((cIdx * 23) % 40)}px`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
};

