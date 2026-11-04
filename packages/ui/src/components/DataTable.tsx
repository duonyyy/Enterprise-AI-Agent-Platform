import React from "react";

export interface Column<T> {
  key: string;
  header: string;
  width?: string;
  align?: "left" | "center" | "right";
  render?: (item: T, index: number) => React.ReactNode;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  keyExtractor: (item: T, index: number) => string;
  onRowClick?: (item: T) => void;
  emptyState?: React.ReactNode;
  caption?: string;
}

export function DataTable<T>({
  columns,
  data,
  keyExtractor,
  onRowClick,
  emptyState,
  caption,
}: DataTableProps<T>) {
  if (data.length === 0 && emptyState) {
    return <div style={{ width: "100%" }}>{emptyState}</div>;
  }

  return (
    <div
      style={{
        width: "100%",
        overflowX: "auto",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--bg-surface)",
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          textAlign: "left",
          fontSize: "13px",
        }}
      >
        {caption && <caption style={{ display: "none" }}>{caption}</caption>}
        <thead>
          <tr
            style={{
              borderBottom: "1px solid var(--border-subtle)",
              backgroundColor: "var(--bg-subtle)",
            }}
          >
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={{
                  padding: "10px 14px",
                  fontSize: "11px",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  color: "var(--text-muted)",
                  width: col.width,
                  textAlign: col.align || "left",
                  whiteSpace: "nowrap",
                }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((item, idx) => {
            const isClickable = Boolean(onRowClick);
            return (
              <tr
                key={keyExtractor(item, idx)}
                onClick={(e) => {
                  const target = e.target as HTMLElement;
                  if (target.closest("button, a, input, select, textarea")) {
                    return;
                  }
                  onRowClick?.(item);
                }}
                style={{
                  borderBottom: idx === data.length - 1 ? "none" : "1px solid var(--border-subtle)",
                  cursor: isClickable ? "pointer" : "default",
                  transition: "background-color var(--transition-fast)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--bg-subtle)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      padding: "12px 14px",
                      color: "var(--text-primary)",
                      textAlign: col.align || "left",
                      verticalAlign: "middle",
                    }}
                  >
                    {col.render ? col.render(item, idx) : (item as Record<string, unknown>)[col.key] as React.ReactNode}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

