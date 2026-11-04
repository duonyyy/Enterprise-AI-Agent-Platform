import React from "react";
import { Button } from "./Button";

export interface StaleBannerProps {
  message?: string;
  onRefresh: () => void;
  isRefreshing?: boolean;
}

export const StaleBanner: React.FC<StaleBannerProps> = ({
  message = "Dữ liệu hiển thị có thể đã cũ do kết nối mạng gián đoạn hoặc có phiên bản mới hơn trên máy chủ.",
  onRefresh,
  isRefreshing = false,
}) => {
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        padding: "10px 16px",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--color-warning-bg)",
        border: "1px solid var(--color-warning-border)",
        color: "var(--color-warning)",
        fontSize: "12px",
        marginBottom: "16px",
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flex: 1, minWidth: "260px" }}>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          style={{ flexShrink: 0 }}
        >
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        <span style={{ color: "var(--text-primary)" }}>{message}</span>
      </div>

      <Button
        variant="secondary"
        size="sm"
        onClick={onRefresh}
        isLoading={isRefreshing}
        style={{
          borderColor: "var(--color-warning-border)",
          color: "var(--color-warning)",
        }}
      >
        Làm mới ngay
      </Button>
    </div>
  );
};

