import React from "react";
import { Button } from "./Button";

export interface ErrorStateProps {
  title?: string;
  message: string;
  statusCode?: number;
  requestId?: string;
  onRetry?: () => void;
  isRetrying?: boolean;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = "Không thể tải dữ liệu",
  message,
  statusCode,
  requestId,
  onRetry,
  isRetrying = false,
}) => {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "40px 24px",
        textAlign: "center",
        border: "1px solid var(--color-danger-border)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--color-danger-bg)",
        width: "100%",
      }}
    >
      <div
        style={{
          width: "48px",
          height: "48px",
          borderRadius: "50%",
          backgroundColor: "rgba(239, 68, 68, 0.2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "14px",
          color: "var(--color-danger)",
        }}
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      </div>

      <h3
        style={{
          fontSize: "15px",
          fontWeight: 600,
          color: "var(--text-primary)",
          marginBottom: "6px",
        }}
      >
        {title}
        {statusCode && (
          <span
            style={{
              marginLeft: "8px",
              fontSize: "12px",
              padding: "1px 6px",
              borderRadius: "var(--radius-xs)",
              backgroundColor: "rgba(239, 68, 68, 0.3)",
              color: "#FCA5A5",
              fontWeight: 500,
            }}
          >
            HTTP {statusCode}
          </span>
        )}
      </h3>

      <p
        style={{
          fontSize: "13px",
          color: "var(--text-secondary)",
          maxWidth: "460px",
          lineHeight: 1.5,
          marginBottom: "16px",
        }}
      >
        {message}
      </p>

      {requestId && (
        <div
          style={{
            fontSize: "11px",
            fontFamily: "var(--font-mono)",
            color: "var(--text-muted)",
            marginBottom: "16px",
            padding: "2px 8px",
            borderRadius: "var(--radius-xs)",
            backgroundColor: "var(--bg-canvas)",
            border: "1px solid var(--border-subtle)",
          }}
        >
          Mã kiểm soát: {requestId}
        </div>
      )}

      {onRetry && (
        <Button variant="danger" size="md" onClick={onRetry} isLoading={isRetrying}>
          Thử lại kết nối
        </Button>
      )}
    </div>
  );
};

