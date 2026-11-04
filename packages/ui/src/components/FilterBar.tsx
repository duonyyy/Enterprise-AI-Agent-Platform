import React from "react";
import { SearchInput } from "./Input";
import { Button } from "./Button";

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  selectedFilter: string;
  onFilterChange: (filter: string) => void;
  filterOptions: FilterOption[];
  onRefresh?: () => void;
  isRefreshing?: boolean;
  totalCount?: number;
  filteredCount?: number;
  rightSlot?: React.ReactNode;
  placeholder?: string;
}

export const FilterBar: React.FC<FilterBarProps> = ({
  searchQuery,
  onSearchChange,
  selectedFilter,
  onFilterChange,
  filterOptions,
  onRefresh,
  isRefreshing = false,
  totalCount,
  filteredCount,
  rightSlot,
  placeholder = "Search by name or slug...",
}) => {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        flexWrap: "wrap",
        marginBottom: "16px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flex: 1,
          minWidth: "280px",
          maxWidth: "600px",
        }}
      >
        <div style={{ flex: 1 }}>
          <SearchInput
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            onClear={() => onSearchChange("")}
            placeholder={placeholder}
          />
        </div>

        {/* Filter dropdown */}
        <select
          value={selectedFilter}
          onChange={(e) => onFilterChange(e.target.value)}
          aria-label="Lọc theo trạng thái"
          style={{
            height: "34px",
            padding: "0 10px",
            fontSize: "12px",
            backgroundColor: "var(--bg-surface)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-sm)",
            cursor: "pointer",
            outline: "none",
          }}
        >
          {filterOptions.map((opt) => (
            <option key={opt.value} value={opt.value} style={{ backgroundColor: "var(--bg-surface)" }}>
              {opt.label}
            </option>
          ))}
        </select>

        {onRefresh && (
          <Button
            variant="secondary"
            size="md"
            onClick={onRefresh}
            isLoading={isRefreshing}
            title="Làm mới danh sách"
            aria-label="Làm mới danh sách"
            style={{ padding: "0 10px" }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          </Button>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        {totalCount !== undefined && (
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            Hiển thị{" "}
            <strong style={{ color: "var(--text-secondary)", fontWeight: 600 }}>
              {filteredCount !== undefined ? filteredCount : totalCount}
            </strong>
            /{totalCount} mục
          </span>
        )}
        {rightSlot}
      </div>
    </div>
  );
};

