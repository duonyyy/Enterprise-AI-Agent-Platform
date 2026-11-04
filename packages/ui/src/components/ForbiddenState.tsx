import React from "react";
import { Button } from "./Button";

export interface ForbiddenStateProps {
  title?: string;
  message?: string;
  onContactAdmin?: () => void;
  onGoBack?: () => void;
}

export const ForbiddenState: React.FC<ForbiddenStateProps> = ({
  title = "Quyền truy cập bị từ chối",
  message = "Bạn không có quyền hạn cần thiết để truy cập hoặc thao tác trên danh mục này trong Không gian làm việc hiện tại.",
  onContactAdmin,
  onGoBack,
}) => {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        textAlign: "center",
        border: "1px solid var(--border-strong)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--bg-surface)",
        width: "100%",
      }}
    >
      <div
        style={{
          width: "52px",
          height: "52px",
          borderRadius: "50%",
          backgroundColor: "rgba(245, 158, 11, 0.15)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "16px",
          color: "var(--color-warning)",
        }}
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
      </div>

      <h3
        style={{
          fontSize: "16px",
          fontWeight: 600,
          color: "var(--text-primary)",
          marginBottom: "8px",
        }}
      >
        {title}
        <span
          style={{
            marginLeft: "8px",
            fontSize: "11px",
            padding: "2px 6px",
            borderRadius: "var(--radius-xs)",
            backgroundColor: "rgba(245, 158, 11, 0.2)",
            color: "var(--color-warning)",
            fontWeight: 600,
          }}
        >
          HTTP 403
        </span>
      </h3>

      <p
        style={{
          fontSize: "13px",
          color: "var(--text-secondary)",
          maxWidth: "460px",
          lineHeight: 1.5,
          marginBottom: "20px",
        }}
      >
        {message}
      </p>

      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        {onGoBack && (
          <Button variant="secondary" size="md" onClick={onGoBack}>
            Quay lại trang trước
          </Button>
        )}
        {onContactAdmin && (
          <Button variant="outline" size="md" onClick={onContactAdmin}>
            Liên hệ Quản trị viên
          </Button>
        )}
      </div>
    </div>
  );
};

