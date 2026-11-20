"use client";

import React, { useMemo } from "react";
import {
  DataTable,
  Button,
  LoadingSkeleton,
  EmptyState,
  ErrorState,
  ForbiddenState,
  Pagination,
  Column,
  BrandIcon,
} from "@enterprise/ui";
import { AgentDraftMetadata } from "@enterprise/api-client";

export type ViewState = "ready" | "loading" | "empty" | "error" | "forbidden" | "stale";
export type ViewMode = "table" | "grid";
export type ExecutionMode = "fixed_plan" | "planner_based" | "react";

export interface StarterTemplate {
  name: string;
  slug: string;
  description: string;
  mode: ExecutionMode;
  modelRef: string;
  instructions: string;
  maxSteps: number;
  maxWallTime: number;
  maxTokens: number;
  badge: string;
  iconType: "doc" | "support" | "audit";
}

export const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    name: "Trợ lý Soạn thảo Báo giá & Hợp đồng",
    slug: "document-drafter-pro",
    description:
      "Tự động phân tích yêu cầu kinh doanh, đối soát điều khoản và soạn thảo dự thảo hợp đồng, báo giá theo chuẩn ISO doanh nghiệp.",
    mode: "fixed_plan",
    modelRef: "",
    instructions:
      "Bạn là Trợ lý Soạn thảo Báo giá & Hợp đồng cấp doanh nghiệp. Nhiệm vụ của bạn là phân tích yêu cầu thương mại, kiểm tra tính tuân thủ pháp lý và xây dựng tài liệu báo giá/hợp đồng chính xác theo mẫu chuẩn được duyệt.",
    maxSteps: 12,
    maxWallTime: 180,
    maxTokens: 16000,
    badge: "Fixed Plan • Cần nhập Model Config UUID",
    iconType: "doc",
  },
  {
    name: "Trợ lý Hỗ trợ Khách hàng & Kỹ thuật",
    slug: "customer-support-assistant",
    description:
      "Tương tác phản hồi thắc mắc kỹ thuật, tra cứu tri thức sản phẩm và phân loại ticket tự động theo kịch bản chuẩn.",
    mode: "fixed_plan",
    modelRef: "",
    instructions:
      "Bạn là Chuyên viên Hỗ trợ Khách hàng AI. Bạn tiếp nhận yêu cầu, tra cứu cơ sở tri thức công ty, giải đáp khúc mắc kỹ thuật và chỉ định mức độ ưu tiên giải quyết sự cố.",
    maxSteps: 15,
    maxWallTime: 240,
    maxTokens: 24000,
    badge: "Fixed Plan • Cần nhập Model Config UUID",
    iconType: "support",
  },
  {
    name: "Chuyên viên Đối soát Dữ liệu Tự động",
    slug: "data-reconciliation-bot",
    description:
      "Thực thi quy trình đối soát dữ liệu báo cáo tài chính, kiểm toán sai lệch số dư và tổng hợp chỉ số KPI định kỳ.",
    mode: "fixed_plan",
    modelRef: "",
    instructions:
      "Bạn là Chuyên viên Đối soát Dữ liệu Tự động. Bạn phân tích đa nguồn dữ liệu theo kế hoạch định sẵn, đối chiếu số dư sổ cái và xuất báo cáo phát hiện dị biệt phục vụ kiểm toán nội bộ.",
    maxSteps: 20,
    maxWallTime: 300,
    maxTokens: 32000,
    badge: "Fixed Plan • Cần nhập Model Config UUID",
    iconType: "audit",
  },
];

export function getAgentVisualProfile(slug: string) {
  let hash = 0;
  for (let i = 0; i < slug.length; i++) {
    hash = slug.charCodeAt(i) + ((hash << 5) - hash);
  }
  const palettes = [
    {
      bg: "linear-gradient(135deg, rgba(15, 118, 110, 0.12) 0%, rgba(15, 118, 110, 0.24) 100%)",
      border: "rgba(15, 118, 110, 0.35)",
      color: "var(--color-primary)",
      dot: "var(--color-primary)",
    },
    {
      bg: "linear-gradient(135deg, rgba(194, 65, 12, 0.12) 0%, rgba(194, 65, 12, 0.24) 100%)",
      border: "rgba(194, 65, 12, 0.35)",
      color: "var(--color-copper)",
      dot: "var(--color-copper)",
    },
    {
      bg: "linear-gradient(135deg, rgba(79, 70, 229, 0.12) 0%, rgba(79, 70, 229, 0.24) 100%)",
      border: "rgba(79, 70, 229, 0.35)",
      color: "#4F46E5",
      dot: "#4F46E5",
    },
    {
      bg: "linear-gradient(135deg, rgba(5, 150, 105, 0.12) 0%, rgba(5, 150, 105, 0.24) 100%)",
      border: "rgba(5, 150, 105, 0.35)",
      color: "var(--color-success)",
      dot: "var(--color-success)",
    },
    {
      bg: "linear-gradient(135deg, rgba(2, 132, 199, 0.12) 0%, rgba(2, 132, 199, 0.24) 100%)",
      border: "rgba(2, 132, 199, 0.35)",
      color: "var(--color-info)",
      dot: "var(--color-info)",
    },
    {
      bg: "linear-gradient(135deg, rgba(217, 119, 6, 0.12) 0%, rgba(217, 119, 6, 0.24) 100%)",
      border: "rgba(217, 119, 6, 0.35)",
      color: "var(--color-warning)",
      dot: "var(--color-warning)",
    },
  ];
  return palettes[Math.abs(hash) % palettes.length];
}

export function getAgentInitials(name: string, slug: string): string {
  const clean = name.trim();
  if (!clean) return slug.slice(0, 2).toUpperCase();
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return clean.slice(0, 2).toUpperCase();
}

export function AgentIdenticon({
  slug,
  name,
  size = "md",
}: {
  slug: string;
  name: string;
  size?: "sm" | "md" | "lg";
}) {
  const profile = useMemo(() => getAgentVisualProfile(slug), [slug]);
  const initials = useMemo(() => getAgentInitials(name, slug), [name, slug]);

  return (
    <div
      className={`agent-identicon identicon-${size}`}
      style={{
        background: profile.bg,
        borderColor: profile.border,
        color: profile.color,
      }}
      aria-hidden="true"
      title={`Tác tử: ${name} (${slug})`}
    >
      <span className="identicon-letters">{initials}</span>
      <span
        className="identicon-indicator"
        style={{ backgroundColor: profile.dot }}
      />
    </div>
  );
}

export function AgentStatusPill({
  archived,
  revision,
}: {
  archived: boolean;
  revision?: number;
}) {
  if (archived) {
    return (
      <span
        className="agent-status-pill status-archived"
        title="Tác tử đã bị lưu trữ (Archived) — Không thể chỉnh sửa hoặc xuất bản"
      >
        <span className="status-dot dot-archived" />
        <span>Archived</span>
      </span>
    );
  }
  return (
    <span
      className="agent-status-pill status-draft"
      title={`Bản nháp đang hoạt động — revision ${revision ?? 1}`}
    >
      <span className="status-dot dot-draft-pulse" />
      <span>Draft {revision ? `rev ${revision}` : ""}</span>
    </span>
  );
}

export function formatDate(isoString?: string | null): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString("vi-VN", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoString;
  }
}

export interface AgentListProps {
  agents: AgentDraftMetadata[];
  filteredAgents: AgentDraftMetadata[];
  viewState: ViewState;
  viewMode: ViewMode;
  onSetViewMode: (mode: ViewMode) => void;
  searchQuery: string;
  onSetSearchQuery: (query: string) => void;
  statusFilter: string;
  onSetStatusFilter: (status: string) => void;
  onResetFilters: () => void;
  onRefresh: () => void;
  isRefreshing: boolean;
  currentPage: number;
  onPageChange: (page: number) => void;
  totalPages: number;
  itemsPerPage: number;
  nextCursor: string | null;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  onOpenEditor: (agent: AgentDraftMetadata) => void;
  onOpenPublish: (agent: AgentDraftMetadata) => void;
  onOpenCreate: () => void;
  onApplyTemplate: (template: StarterTemplate) => void;
  searchFieldRef: React.RefObject<HTMLInputElement>;
  errorMessage?: string;
  errorStatus?: number;
  requestId?: string;
  onContactAdmin: () => void;
}

export function AgentList({
  agents,
  filteredAgents,
  viewState,
  viewMode,
  onSetViewMode,
  searchQuery,
  onSetSearchQuery,
  statusFilter,
  onSetStatusFilter,
  onResetFilters,
  onRefresh,
  isRefreshing,
  currentPage,
  onPageChange,
  totalPages,
  itemsPerPage,
  nextCursor,
  isLoadingMore,
  onLoadMore,
  onOpenEditor,
  onOpenPublish,
  onOpenCreate,
  onApplyTemplate,
  searchFieldRef,
  errorMessage,
  errorStatus,
  requestId,
  onContactAdmin,
}: AgentListProps) {
  const isFilteringActive = searchQuery.trim() !== "" || statusFilter !== "ALL";

  const paginatedAgents = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return filteredAgents.slice(start, start + itemsPerPage);
  }, [filteredAgents, currentPage, itemsPerPage]);

  const columns: Column<AgentDraftMetadata>[] = useMemo(
    () => [
      {
        key: "display_name",
        header: "AI Agent & Identity",
        width: "42%",
        render: (item) => (
          <div className="agent-identity-cell">
            <AgentIdenticon slug={item.slug} name={item.display_name} size="md" />
            <div className="agent-identity-info">
              <span className="agent-title-text">{item.display_name}</span>
              <span className="agent-slug-badge">{item.slug}</span>
            </div>
          </div>
        ),
      },
      {
        key: "revision",
        header: "Revision",
        width: "14%",
        render: (item) => <span className="agent-rev-token">rev {item.revision}</span>,
      },
      {
        key: "status",
        header: "Lifecycle Status",
        width: "16%",
        render: (item) => (
          <AgentStatusPill archived={Boolean(item.archived_at)} revision={item.revision} />
        ),
      },
      {
        key: "created_at",
        header: "Created At",
        width: "16%",
        render: (item) => (
          <span className="agent-date-text">{formatDate(item.created_at)}</span>
        ),
      },
      {
        key: "actions",
        header: "Actions",
        width: "12%",
        align: "right",
        render: (item) => (
          <div className="agent-actions-row">
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                onOpenEditor(item);
              }}
              title="Chỉnh sửa bản nháp Tác tử (Agent Studio Workshop)"
              aria-label={`Chỉnh sửa bản nháp tác tử ${item.display_name}`}
            >
              Chỉnh sửa
            </Button>
            <Button
              variant="copper"
              size="sm"
              disabled={Boolean(item.archived_at)}
              onClick={(e) => {
                e.stopPropagation();
                onOpenPublish(item);
              }}
              title={
                item.archived_at
                  ? "Tác tử đã bị lưu trữ, không thể xuất bản"
                  : "Xuất bản phiên bản bất biến (Step 04-C)"
              }
              aria-label={
                item.archived_at
                  ? `Tác tử ${item.display_name} đã bị lưu trữ, không thể xuất bản`
                  : `Xuất bản phiên bản bất biến cho tác tử ${item.display_name}`
              }
            >
              <BrandIcon name="action-publish" size={13} color="currentColor" />
              <span>Publish</span>
            </Button>
          </div>
        ),
      },
    ],
    [onOpenEditor, onOpenPublish]
  );

  return (
    <>
      {/* Advanced Filter & Console Toolbar */}
      {viewState !== "error" && viewState !== "forbidden" && (
        <section
          className="agents-console-toolbar"
          aria-label="Thanh công cụ tìm kiếm và lọc tác tử"
        >
          <div className="toolbar-search-filter-group">
            <div className="toolbar-search-input">
              <svg
                aria-hidden="true"
                className="search-leading-icon"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                ref={searchFieldRef}
                id="agent-search-input"
                type="search"
                className="search-field"
                placeholder="Tìm tác tử theo tên hoặc định danh slug... (Nhấn / hoặc ⌘K)"
                value={searchQuery}
                onChange={(e) => onSetSearchQuery(e.target.value)}
                aria-label="Tìm kiếm tác tử theo tên hoặc định danh slug"
                aria-describedby="search-keyboard-hint search-count-announcement"
                role="searchbox"
              />
              <kbd
                id="search-keyboard-hint"
                className="search-shortcut-badge"
                aria-hidden="true"
              >
                /
              </kbd>
              {searchQuery && (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => {
                    onSetSearchQuery("");
                    searchFieldRef.current?.focus();
                  }}
                  aria-label="Xóa từ khóa tìm kiếm"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Screen Reader Live Region for Search Results Count */}
            <div
              id="search-count-announcement"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              className="visually-hidden"
            >
              {nextCursor
                ? `Đang hiển thị ${filteredAgents.length} tác tử đã tải.`
                : `Đang hiển thị ${filteredAgents.length} trên tổng số ${agents.length} tác tử.`}
            </div>

            {/* Quick Status Dropdown */}
            <label htmlFor="agent-status-filter" className="visually-hidden">
              Lọc theo trạng thái tác tử
            </label>
            <select
              id="agent-status-filter"
              className="toolbar-select"
              value={statusFilter}
              onChange={(e) => onSetStatusFilter(e.target.value)}
              aria-label="Lọc tác tử theo trạng thái"
            >
              <option value="ALL">Tất cả trạng thái</option>
              <option value="DRAFT">Bản nháp (Draft)</option>
              <option value="ARCHIVED">Đã lưu trữ (Archived)</option>
            </select>

            {/* Refresh Button */}
            <Button
              variant="secondary"
              size="md"
              onClick={onRefresh}
              isLoading={isRefreshing}
              title="Làm mới danh sách tác tử từ máy chủ"
              aria-label="Làm mới danh sách tác tử từ máy chủ"
            >
              <svg
                aria-hidden="true"
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
              >
                <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
              </svg>
              <span>Làm mới</span>
            </Button>
          </div>

          <div className="toolbar-view-switcher-group">
            {isFilteringActive && (
              <button
                type="button"
                className="filter-reset-link"
                onClick={onResetFilters}
                aria-label={`Xóa bộ lọc tìm kiếm, hiển thị toàn bộ ${agents.length} tác tử`}
              >
                Xóa bộ lọc ({filteredAgents.length}/{agents.length})
              </button>
            )}

            {/* View Mode Toggle: Compact Table vs Grid Cards */}
            <div
              className="view-mode-segmented-control"
              role="group"
              aria-label="Chế độ hiển thị danh sách tác tử"
            >
              <button
                type="button"
                className={`view-mode-button ${viewMode === "table" ? "active" : ""}`}
                onClick={() => onSetViewMode("table")}
                title="Dạng bảng chi tiết (Compact Table)"
                aria-label="Hiển thị dạng bảng chi tiết (Compact Table)"
                aria-pressed={viewMode === "table"}
              >
                <svg
                  aria-hidden="true"
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <line x1="3" y1="9" x2="21" y2="9" />
                  <line x1="3" y1="15" x2="21" y2="15" />
                  <line x1="9" y1="3" x2="9" y2="21" />
                </svg>
                <span>Bảng</span>
              </button>
              <button
                type="button"
                className={`view-mode-button ${viewMode === "grid" ? "active" : ""}`}
                onClick={() => onSetViewMode("grid")}
                title="Dạng lưới thẻ (Grid Cards)"
                aria-label="Hiển thị dạng lưới thẻ (Grid Cards)"
                aria-pressed={viewMode === "grid"}
              >
                <svg
                  aria-hidden="true"
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="14" width="7" height="7" rx="1" />
                  <rect x="3" y="14" width="7" height="7" rx="1" />
                </svg>
                <span>Lưới</span>
              </button>
            </div>
          </div>
        </section>
      )}

      {/* View State Rendering */}
      {viewState === "loading" && (
        <div className="agents-loading-container">
          {viewMode === "table" ? (
            <LoadingSkeleton rows={5} columns={5} />
          ) : (
            <div className="skeleton-cards-grid">
              {Array.from({ length: 6 }).map((_, idx) => (
                <div key={idx} className="skeleton-agent-card">
                  <div className="skeleton-card-top">
                    <div className="skeleton-shimmer skeleton-circle-avatar" />
                    <div className="skeleton-title-lines">
                      <div className="skeleton-shimmer skeleton-line-title" />
                      <div className="skeleton-shimmer skeleton-line-slug" />
                    </div>
                  </div>
                  <div className="skeleton-card-middle">
                    <div className="skeleton-shimmer skeleton-badge" />
                    <div className="skeleton-shimmer skeleton-badge-sm" />
                  </div>
                  <div className="skeleton-card-bottom">
                    <div className="skeleton-shimmer skeleton-btn" />
                    <div className="skeleton-shimmer skeleton-btn-primary" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {viewState === "error" && (
        <ErrorState
          title="Không thể tải danh sách Tác tử AI"
          message={errorMessage || "Đã xảy ra lỗi khi kết nối tới máy chủ Control Plane."}
          statusCode={errorStatus}
          requestId={requestId}
          onRetry={onRefresh}
          isRetrying={isRefreshing}
        />
      )}

      {viewState === "forbidden" && (
        <ForbiddenState
          title="Truy cập bị từ chối"
          message="Tài khoản của bạn không có vai trò 'agent:read' trong Không gian làm việc này."
          onGoBack={onRefresh}
          onContactAdmin={onContactAdmin}
        />
      )}

      {/* Truly Empty State: Workspace has 0 agents */}
      {viewState === "empty" && agents.length === 0 && (
        <div className="agents-empty-wrapper">
          <EmptyState
            title="Chưa có Tác tử AI nào trong không gian này"
            description="Bắt đầu bằng cách tạo bản nháp tác tử đầu tiên hoặc chọn nhanh một trong các mẫu chuẩn doanh nghiệp dưới đây."
            actionLabel="+ Khởi tạo Tác tử Mới"
            onAction={onOpenCreate}
          />

          <div className="starter-templates-section">
            <div className="starter-templates-heading">
              <span className="templates-heading-line" />
              <span className="templates-heading-text">
                GỢI Ý MẪU TÁC TỬ DOANH NGHIỆP CHUẨN
              </span>
              <span className="templates-heading-line" />
            </div>

            <div className="starter-templates-grid">
              {STARTER_TEMPLATES.map((tmpl) => (
                <div
                  key={tmpl.slug}
                  className="starter-template-card"
                  onClick={() => onApplyTemplate(tmpl)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onApplyTemplate(tmpl);
                    }
                  }}
                  aria-label={`Áp dụng mẫu tác tử ${tmpl.name}`}
                >
                  <div className="template-card-header">
                    <AgentIdenticon slug={tmpl.slug} name={tmpl.name} size="sm" />
                    <span className="template-mode-pill">{tmpl.badge}</span>
                  </div>
                  <h4 className="template-card-title">{tmpl.name}</h4>
                  <p className="template-card-desc">{tmpl.description}</p>
                  <div className="template-card-footer">
                    <span className="template-apply-action">Áp dụng mẫu này →</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Ready / Stale View or Filtered Empty View */}
      {(viewState === "ready" || viewState === "stale" || (viewState === "empty" && agents.length > 0)) && (
        <>
          {viewMode === "table" ? (
            <div className="agent-desktop-table-card">
              <DataTable
                columns={columns}
                data={paginatedAgents}
                keyExtractor={(item) => item.agent_id}
                caption="Danh sách Tác tử AI trong không gian làm việc"
                emptyState={
                  <EmptyState
                    title="Không tìm thấy tác tử phù hợp"
                    description={`Không có tác tử nào khớp với bộ lọc hiện tại. Xóa bộ lọc tìm kiếm để xem tất cả.`}
                    actionLabel="Xóa bộ lọc"
                    onAction={onResetFilters}
                  />
                }
              />
            </div>
          ) : (
            <div className="agent-grid-cards-container">
              {paginatedAgents.length === 0 ? (
                <EmptyState
                  title="Không tìm thấy tác tử phù hợp"
                  description={`Không có tác tử nào khớp với bộ lọc hiện tại. Xóa bộ lọc tìm kiếm để xem tất cả.`}
                  actionLabel="Xóa bộ lọc"
                  onAction={onResetFilters}
                />
              ) : (
                <div className="agents-cards-grid">
                  {paginatedAgents.map((agent) => {
                    const isArchived = Boolean(agent.archived_at);
                    return (
                      <div key={agent.agent_id} className="agent-b2b-card">
                        <div className="card-top-identity">
                          <AgentIdenticon
                            slug={agent.slug}
                            name={agent.display_name}
                            size="lg"
                          />
                          <div className="card-title-group">
                            <h3 className="card-agent-name" title={agent.display_name}>
                              {agent.display_name}
                            </h3>
                            <span className="card-agent-slug">{agent.slug}</span>
                          </div>
                        </div>

                        <div className="card-meta-section">
                          <div className="card-status-badges">
                            <AgentStatusPill
                              archived={isArchived}
                              revision={agent.revision}
                            />
                            <span className="card-rev-badge">rev {agent.revision}</span>
                          </div>

                          <div className="card-date-info">
                            <svg
                              width="12"
                              height="12"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              aria-hidden="true"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <polyline points="12 6 12 12 16 14" />
                            </svg>
                            <span>Tạo lúc: {formatDate(agent.created_at)}</span>
                          </div>
                        </div>

                        <div className="card-action-footer">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onOpenEditor(agent)}
                            title={`Chỉnh sửa bản nháp tác tử ${agent.display_name}`}
                            aria-label={`Chỉnh sửa bản nháp tác tử ${agent.display_name}`}
                          >
                            Chỉnh sửa
                          </Button>
                          <Button
                            variant="copper"
                            size="sm"
                            disabled={isArchived}
                            onClick={() => onOpenPublish(agent)}
                            title={
                              isArchived
                                ? "Tác tử đã bị lưu trữ, không thể xuất bản"
                                : `Xuất bản phiên bản bất biến cho tác tử ${agent.display_name}`
                            }
                            aria-label={
                              isArchived
                                ? `Tác tử ${agent.display_name} đã bị lưu trữ, không thể xuất bản`
                                : `Xuất bản phiên bản bất biến cho tác tử ${agent.display_name}`
                            }
                          >
                            <BrandIcon name="action-publish" size={13} color="currentColor" />
                            <span>Publish</span>
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          <Pagination
            currentPage={currentPage}
            totalPages={totalPages}
            onPageChange={onPageChange}
            totalItems={filteredAgents.length}
            itemsPerPage={itemsPerPage}
          />

          {nextCursor && (
            <div className="load-more-container">
              <Button disabled={isLoadingMore} onClick={onLoadMore}>
                {isLoadingMore ? "Đang tải…" : "Tải thêm tác tử"}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}
