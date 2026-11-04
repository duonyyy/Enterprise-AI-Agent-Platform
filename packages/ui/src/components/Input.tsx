import React, { useId } from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  helperText?: string;
  errorText?: string;
  leftIcon?: React.ReactNode;
  rightElement?: React.ReactNode;
}

export const Input: React.FC<InputProps> = ({
  label,
  helperText,
  errorText,
  leftIcon,
  rightElement,
  id,
  style,
  disabled,
  ...props
}) => {
  const generatedId = useId();
  const inputId = id || generatedId;
  const errorId = `${inputId}-error`;
  const helperId = `${inputId}-helper`;

  const hasError = Boolean(errorText);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px", width: "100%" }}>
      {label && (
        <label
          htmlFor={inputId}
          style={{
            fontSize: "12px",
            fontWeight: 500,
            color: hasError ? "var(--color-danger)" : "var(--text-secondary)",
          }}
        >
          {label}
        </label>
      )}
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          width: "100%",
        }}
      >
        {leftIcon && (
          <span
            style={{
              position: "absolute",
              left: "10px",
              display: "flex",
              alignItems: "center",
              color: "var(--text-muted)",
              pointerEvents: "none",
            }}
          >
            {leftIcon}
          </span>
        )}
        <input
          id={inputId}
          disabled={disabled}
          aria-invalid={hasError}
          aria-describedby={hasError ? errorId : helperText ? helperId : undefined}
          style={{
            width: "100%",
            height: "34px",
            paddingLeft: leftIcon ? "32px" : "12px",
            paddingRight: rightElement ? "36px" : "12px",
            fontSize: "13px",
            backgroundColor: "var(--bg-surface)",
            color: "var(--text-primary)",
            border: `1px solid ${hasError ? "var(--color-danger)" : "var(--border-strong)"}`,
            borderRadius: "var(--radius-sm)",
            outline: "none",
            transition: "border-color var(--transition-fast)",
            ...style,
          }}
          {...props}
        />
        {rightElement && (
          <div
            style={{
              position: "absolute",
              right: "8px",
              display: "flex",
              alignItems: "center",
            }}
          >
            {rightElement}
          </div>
        )}
      </div>
      {hasError ? (
        <span id={errorId} style={{ fontSize: "11px", color: "var(--color-danger)" }} role="alert">
          {errorText}
        </span>
      ) : helperText ? (
        <span id={helperId} style={{ fontSize: "11px", color: "var(--text-muted)" }}>
          {helperText}
        </span>
      ) : null}
    </div>
  );
};

export interface SearchInputProps extends Omit<InputProps, "leftIcon" | "rightElement"> {
  onClear?: () => void;
}

export const SearchInput: React.FC<SearchInputProps> = ({
  value,
  onChange,
  onClear,
  placeholder = "Tìm kiếm...",
  ...props
}) => {
  const showClear = Boolean(value && String(value).length > 0);

  const searchIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );

  const clearButton = showClear ? (
    <button
      type="button"
      onClick={onClear}
      aria-label="Xóa nội dung tìm kiếm"
      style={{
        padding: "4px",
        color: "var(--text-muted)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: "pointer",
        borderRadius: "var(--radius-xs)",
      }}
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    </button>
  ) : null;

  return (
    <Input
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      leftIcon={searchIcon}
      rightElement={clearButton}
      {...props}
    />
  );
};

