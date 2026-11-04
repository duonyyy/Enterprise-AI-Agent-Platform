import React from "react";
import { Button } from "./Button";

export interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  totalItems?: number;
  itemsPerPage?: number;
}

export const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  onPageChange,
  totalItems,
  itemsPerPage = 10,
}) => {
  if (totalPages <= 1 && !totalItems) return null;

  const startItem = (currentPage - 1) * itemsPerPage + 1;
  const endItem = totalItems ? Math.min(currentPage * itemsPerPage, totalItems) : currentPage * itemsPerPage;

  return (
    <nav
      aria-label="Phân trang"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 14px",
        borderTop: "1px solid var(--border-subtle)",
        fontSize: "12px",
        color: "var(--text-muted)",
        flexWrap: "wrap",
        gap: "12px",
      }}
    >
      <div>
        {totalItems !== undefined ? (
          <span>
            Showing <strong style={{ color: "var(--text-secondary)" }}>{startItem}</strong> to{" "}
            <strong style={{ color: "var(--text-secondary)" }}>{endItem}</strong> of{" "}
            <strong style={{ color: "var(--text-secondary)" }}>{totalItems}</strong> items
          </span>
        ) : (
          <span>
            Page {currentPage} of {totalPages}
          </span>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <Button
          variant="outline"
          size="sm"
          disabled={currentPage <= 1}
          onClick={() => onPageChange(currentPage - 1)}
          aria-label="Previous page"
        >
          Previous
        </Button>
        <span style={{ padding: "0 6px", color: "var(--text-secondary)", fontWeight: 500 }}>
          {currentPage} / {Math.max(1, totalPages)}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={currentPage >= totalPages}
          onClick={() => onPageChange(currentPage + 1)}
          aria-label="Next page"
        >
          Next
        </Button>
      </div>
    </nav>
  );
};

