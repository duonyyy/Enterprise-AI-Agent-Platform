import React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost" | "outline" | "copper";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = "secondary",
  size = "md",
  isLoading = false,
  leftIcon,
  rightIcon,
  disabled,
  style,
  ...props
}) => {
  const isDisabled = disabled || isLoading;

  const sizeStyles: Record<string, React.CSSProperties> = {
    sm: { height: "28px", padding: "0 10px", fontSize: "12px", gap: "6px" },
    md: { height: "34px", padding: "0 14px", fontSize: "13px", gap: "8px" },
    lg: { height: "40px", padding: "0 18px", fontSize: "14px", gap: "10px" },
  };

  const variantStyles: Record<string, React.CSSProperties> = {
    primary: {
      backgroundColor: "var(--color-primary)",
      color: "var(--color-primary-contrast)",
      border: "1px solid var(--color-primary)",
      fontWeight: 500,
    },
    copper: {
      backgroundColor: "var(--color-copper)",
      color: "#FFFFFF",
      border: "1px solid var(--color-copper)",
      fontWeight: 500,
    },
    secondary: {
      backgroundColor: "var(--bg-surface)",
      color: "var(--text-primary)",
      border: "1px solid var(--border-strong)",
      fontWeight: 500,
    },
    outline: {
      backgroundColor: "transparent",
      color: "var(--text-primary)",
      border: "1px solid var(--border-strong)",
      fontWeight: 500,
    },
    ghost: {
      backgroundColor: "transparent",
      color: "var(--text-secondary)",
      border: "1px solid transparent",
      fontWeight: 400,
    },
    danger: {
      backgroundColor: "var(--color-danger)",
      color: "#FFFFFF",
      border: "1px solid var(--color-danger)",
      fontWeight: 500,
    },
  };

  return (
    <button
      disabled={isDisabled}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "var(--radius-sm)",
        cursor: isDisabled ? "not-allowed" : "pointer",
        opacity: isDisabled ? 0.6 : 1,
        transition: "all var(--transition-fast)",
        userSelect: "none",
        whiteSpace: "nowrap",
        ...sizeStyles[size],
        ...variantStyles[variant],
        ...style,
      }}
      {...props}
    >
      {isLoading ? (
        <span
          style={{
            width: "14px",
            height: "14px",
            border: "2px solid currentColor",
            borderRightColor: "transparent",
            borderRadius: "50%",
            display: "inline-block",
            animation: "spin 0.6s linear infinite",
          }}
          aria-hidden="true"
        />
      ) : (
        leftIcon && <span style={{ display: "inline-flex" }}>{leftIcon}</span>
      )}
      <span>{children}</span>
      {!isLoading && rightIcon && <span style={{ display: "inline-flex" }}>{rightIcon}</span>}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </button>
  );
};

