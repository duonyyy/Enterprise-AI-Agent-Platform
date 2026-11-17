"use client";

import React, {
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
  useLayoutEffect,
} from "react";
import { useRouter } from "next/navigation";
import {
  PageHeader,
  DataTable,
  StatusBadge,
  Button,
  LoadingSkeleton,
  EmptyState,
  ErrorState,
  ForbiddenState,
  StaleBanner,
  Modal,
  Pagination,
  Input,
  Column,
  BrandIcon,
} from "@enterprise/ui";
import {
  defaultApiClient,
  AgentDraftMetadata,
  AgentDraft,
  AgentValidationResult,
  PublishResponse,
  ApiClientError,
} from "@enterprise/api-client";

import {
  AgentPublishScope,
  AgentRequestScope,
  AgentValidationScope,
  clearPendingPublishIntent,
  getPendingPublishIntent,
  isValidationResultStale,
  mergeAgentDraftEdits,
  savePendingPublishIntent,
  estimateTokenCount,
  detectPromptSafetyRisk,
  categorizeDiagnostics,
  compareDraftStates,
  CategorizedDiagnostic,
} from "../../../../../lib/agent-draft-state";

type ViewState = "ready" | "loading" | "empty" | "error" | "forbidden" | "stale";
type ViewMode = "table" | "grid";
type ExecutionMode = "fixed_plan" | "planner_based" | "react";
type ToastType = "success" | "error" | "info";
type WorkshopTab = "persona" | "model" | "dependencies";

interface ToastNotification {
  id: number;
  message: string;
  type: ToastType;
}

interface StarterTemplate {
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

const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    name: "Trợ lý Soạn thảo Báo giá & Hợp đồng",
    slug: "document-drafter-pro",
    description:
      "Tự động phân tích yêu cầu kinh doanh, đối soát điều khoản và soạn thảo dự thảo hợp đồng, báo giá theo chuẩn ISO doanh nghiệp.",
    mode: "fixed_plan",
    modelRef: "approved-general@2",
    instructions:
      "Bạn là Trợ lý Soạn thảo Báo giá & Hợp đồng cấp doanh nghiệp. Nhiệm vụ của bạn là phân tích yêu cầu thương mại, kiểm tra tính tuân thủ pháp lý và xây dựng tài liệu báo giá/hợp đồng chính xác theo mẫu chuẩn được duyệt.",
    maxSteps: 12,
    maxWallTime: 180,
    maxTokens: 16000,
    badge: "Fixed Plan • Approved General",
    iconType: "doc",
  },
  {
    name: "Trợ lý Hỗ trợ Khách hàng & Kỹ thuật",
    slug: "customer-support-react",
    description:
      "Tương tác phản hồi thắc mắc kỹ thuật, tra cứu tri thức sản phẩm và phân loại ticket tự động theo kịch bản chuẩn.",
    mode: "react",
    modelRef: "claude-3-5-sonnet",
    instructions:
      "Bạn là Chuyên viên Hỗ trợ Khách hàng AI. Bạn tiếp nhận yêu cầu, tra cứu cơ sở tri thức công ty, giải đáp khúc mắc kỹ thuật và chỉ định mức độ ưu tiên giải quyết sự cố.",
    maxSteps: 15,
    maxWallTime: 240,
    maxTokens: 24000,
    badge: "ReAct • Claude 3.5 Sonnet",
    iconType: "support",
  },
  {
    name: "Chuyên viên Đối soát Dữ liệu Tự động",
    slug: "data-reconciliation-bot",
    description:
      "Lập kế hoạch nhiều bước đối soát dữ liệu báo cáo tài chính, kiểm toán sai lệch số dư và tổng hợp chỉ số KPI định kỳ.",
    mode: "planner_based",
    modelRef: "gpt-4o",
    instructions:
      "Bạn là Chuyên viên Đối soát Dữ liệu Tự động. Bạn lập kế hoạch phân tích đa nguồn dữ liệu, đối chiếu số dư sổ cái và xuất báo cáo phát hiện dị biệt phục vụ kiểm toán nội bộ.",
    maxSteps: 20,
    maxWallTime: 300,
    maxTokens: 32000,
    badge: "Planner-based • GPT-4o",
    iconType: "audit",
  },
];

// Helper: Visual Identicon Palette from Slug
function getAgentVisualProfile(slug: string) {
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

// Helper: Initials from Agent Display Name
function getAgentInitials(name: string, slug: string): string {
  const clean = name.trim();
  if (!clean) return slug.slice(0, 2).toUpperCase();
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return clean.slice(0, 2).toUpperCase();
}

// Helper: Relative Time Formatter for Intent Recovery
function formatRelativeTime(timestamp: number | null): string {
  if (!timestamp) return "gần đây";
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (elapsedSeconds < 45) return "vừa xong";
  const minutes = Math.floor(elapsedSeconds / 60);
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  const days = Math.floor(hours / 24);
  return `${days} ngày trước`;
}

// Identicon Component
function AgentIdenticon({
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

// Lifecycle Status Pill Component
function AgentStatusPill({
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

export default function AgentsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";
  const router = useRouter();

  // Scopes (R04C-04, R04C-05, Concurrency Isolation)
  const requestScope = useRef(new AgentRequestScope());
  const editorScope = useRef(new AgentRequestScope());
  const validationScope = useRef(new AgentValidationScope());
  const publishScope = useRef(new AgentPublishScope());

  useLayoutEffect(() => {
    const scope = requestScope.current;
    const editor = editorScope.current;
    const validation = validationScope.current;
    const publish = publishScope.current;
    scope.reset();
    return () => {
      scope.invalidate();
      editor.invalidate();
      validation.invalidate();
      publish.invalidate();
    };
  }, [workspaceId]);

  // Data & State
  const [agents, setAgents] = useState<AgentDraftMetadata[]>([]);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [errorStatus, setErrorStatus] = useState<number | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  // View Mode: Compact Table vs Grid Cards
  const [viewMode, setViewMode] = useState<ViewMode>("table");

  // Filters & Search
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [modeFilter, setModeFilter] = useState("ALL");
  const [providerFilter, setProviderFilter] = useState("ALL");
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const itemsPerPage = 8;

  // Create Modal
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentSlug, setNewAgentSlug] = useState("");
  const [newInstructions, setNewInstructions] = useState("");
  const [newModelRef, setNewModelRef] = useState("");
  const [newMode, setNewMode] = useState<ExecutionMode>("fixed_plan");
  const [newMaxSteps, setNewMaxSteps] = useState(10);
  const [newMaxWallTime, setNewMaxWallTime] = useState(180);
  const [newMaxTokens, setNewMaxTokens] = useState(16000);
  const [createError, setCreateError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Editor Drawer / Studio Workshop
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [activeWorkshopTab, setActiveWorkshopTab] =
    useState<WorkshopTab>("persona");
  const [activeDraft, setActiveDraft] = useState<AgentDraft | null>(null);
  const [editorInstructions, setEditorInstructions] = useState("");
  const [editorModelRef, setEditorModelRef] = useState("");
  const [editorMode, setEditorMode] = useState<ExecutionMode>("fixed_plan");
  const [editorMaxSteps, setEditorMaxSteps] = useState(10);
  const [editorMaxWallTime, setEditorMaxWallTime] = useState(180);
  const [editorMaxTokens, setEditorMaxTokens] = useState(16000);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Conflict 412 State with Side-by-Side Diff Data
  const [conflictState, setConflictState] = useState<{
    message: string;
    serverRevision?: number;
    serverDraft?: AgentDraft | null;
  } | null>(null);

  // Validation State (04-B)
  const [isValidating, setIsValidating] = useState(false);
  const [validationResult, setValidationResult] =
    useState<AgentValidationResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [hasEditsSinceValidation, setHasEditsSinceValidation] = useState(false);

  // Gutter scroll reference for Code Editor
  const gutterRef = useRef<HTMLDivElement>(null);
  const handleTextareaScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
    if (gutterRef.current) {
      gutterRef.current.scrollTop = e.currentTarget.scrollTop;
    }
  };

  const invalidateValidation = useCallback(() => {
    validationScope.current.invalidate();
    setIsValidating(false);
    setValidationError(null);
    setHasEditsSinceValidation(true);
  }, []);

  const closeEditor = useCallback(() => {
    editorScope.current.invalidate();
    invalidateValidation();
    setIsEditorOpen(false);
    setActiveWorkshopTab("persona");
    setActiveDraft(null);
    setConflictState(null);
    setSaveError(null);
    setValidationResult(null);
    setHasEditsSinceValidation(false);
  }, [invalidateValidation]);

  // Focus and trigger references for accessibility (WCAG 2.2 Operable)
  const searchFieldRef = useRef<HTMLInputElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);

  // Dirty state checks (WCAG 2.2 Operable - Data Preservation on Esc/Close)
  const isCreateDirty = useMemo(() => {
    return Boolean(
      newAgentName.trim() ||
        newAgentSlug.trim() ||
        newInstructions.trim() ||
        newModelRef.trim()
    );
  }, [newAgentName, newAgentSlug, newInstructions, newModelRef]);

  const handleOpenCreateModal = useCallback(() => {
    setCreateError("");
    setIsCreateModalOpen(true);
  }, []);

  const handleCloseCreateModal = useCallback(() => {
    if (isCreateDirty) {
      const confirmed = window.confirm(
        "Bạn có thông tin chưa lưu trong biểu mẫu tạo tác tử. Bạn có chắc chắn muốn hủy và đóng?"
      );
      if (!confirmed) return;
    }
    setIsCreateModalOpen(false);
    setCreateError("");
  }, [isCreateDirty]);

  const isEditorDirty = useMemo(() => {
    if (!activeDraft) return false;
    const spec = (activeDraft.spec as Record<string, unknown>) || {};
    const persona = (spec.persona as { instructions?: string }) || {};
    const limits = (spec.limits as {
      maxSteps?: number;
      maxWallTimeSeconds?: number;
      maxTotalTokens?: number;
    }) || {};

    const origInstructions = persona.instructions ?? "";
    const origModel = (spec.modelRef as string) ?? "";
    const origMode = (spec.mode as ExecutionMode) ?? "fixed_plan";
    const origSteps = limits.maxSteps ?? 10;
    const origWallTime = limits.maxWallTimeSeconds ?? 180;
    const origTokens = limits.maxTotalTokens ?? 16000;

    return (
      editorInstructions !== origInstructions ||
      editorModelRef !== origModel ||
      editorMode !== origMode ||
      editorMaxSteps !== origSteps ||
      editorMaxWallTime !== origWallTime ||
      editorMaxTokens !== origTokens
    );
  }, [
    activeDraft,
    editorInstructions,
    editorModelRef,
    editorMode,
    editorMaxSteps,
    editorMaxWallTime,
    editorMaxTokens,
  ]);

  const handleCloseEditor = useCallback(() => {
    if (isEditorDirty) {
      const confirmed = window.confirm(
        "Bạn có các chỉnh sửa cấu hình chưa lưu trong Agent Studio Workshop. Bạn có chắc chắn muốn hủy và đóng?"
      );
      if (!confirmed) return;
    }
    closeEditor();
  }, [isEditorDirty, closeEditor]);

  // Workshop Tab Keyboard Navigation (WAI-ARIA Tabs roving tabindex pattern)
  const handleTabKeyDown = (
    e: React.KeyboardEvent,
    currentTab: WorkshopTab
  ) => {
    const tabs: WorkshopTab[] = ["persona", "model", "dependencies"];
    const currentIndex = tabs.indexOf(currentTab);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      const nextTab = tabs[(currentIndex + 1) % tabs.length];
      setActiveWorkshopTab(nextTab);
      document.getElementById(`workshop-tab-${nextTab}`)?.focus();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      const prevTab = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
      setActiveWorkshopTab(prevTab);
      document.getElementById(`workshop-tab-${prevTab}`)?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveWorkshopTab(tabs[0]);
      document.getElementById(`workshop-tab-${tabs[0]}`)?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveWorkshopTab(tabs[tabs.length - 1]);
      document.getElementById(`workshop-tab-${tabs[tabs.length - 1]}`)?.focus();
    }
  };

  const validationIsStale =
    validationResult !== null &&
    (validationResult.agent_id !== activeDraft?.agent_id ||
      isValidationResultStale(
        validationResult.revision,
        activeDraft?.revision ?? 1,
        hasEditsSinceValidation
      ));
  const validationReady =
    validationResult?.ready === true && !validationIsStale;

  // Categorized Diagnostics
  const categorizedDiagnostics = useMemo(() => {
    if (!validationResult || !validationResult.diagnostics) return [];
    return categorizeDiagnostics(validationResult.diagnostics);
  }, [validationResult]);

  const criticalIssuesCount = useMemo(() => {
    return categorizedDiagnostics.filter((d) => d.severity === "critical")
      .length;
  }, [categorizedDiagnostics]);

  const warningIssuesCount = useMemo(() => {
    return categorizedDiagnostics.filter((d) => d.severity === "warning")
      .length;
  }, [categorizedDiagnostics]);

  // Focus Diagnostic Field Action
  const handleFocusDiagnosticField = (diagnostic: CategorizedDiagnostic) => {
    setActiveWorkshopTab(diagnostic.tabTarget);
    setTimeout(() => {
      let elementId = "";
      if (
        diagnostic.field.includes("persona") ||
        diagnostic.field.includes("instruction")
      ) {
        elementId = "edit-instructions";
      } else if (diagnostic.field.includes("model")) {
        elementId = "edit-model-ref";
      } else if (diagnostic.field.includes("mode")) {
        elementId = "edit-mode-select";
      } else if (diagnostic.field.includes("step")) {
        elementId = "edit-max-steps-input";
      } else if (
        diagnostic.field.includes("time") ||
        diagnostic.field.includes("wall")
      ) {
        elementId = "edit-max-walltime-input";
      } else if (diagnostic.field.includes("token")) {
        elementId = "edit-max-tokens-input";
      }

      if (elementId) {
        const el = document.getElementById(elementId);
        if (el) {
          el.focus();
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("field-focus-pulse");
          setTimeout(() => el.classList.remove("field-focus-pulse"), 2500);
        }
      }
    }, 80);
  };

  // ========================================================
  // PUBLISH STATE & DIALOG (04-C, R04C-04, R04C-05, High-Trust FinTech Stepper)
  // ========================================================
  const [publishTargetAgent, setPublishTargetAgent] =
    useState<AgentDraftMetadata | null>(null);
  const [publishIdempotencyKey, setPublishIdempotencyKey] =
    useState<string>("");
  const [publishRevision, setPublishRevision] = useState<number>(1);
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishStep, setPublishStep] = useState<1 | 2 | 3>(1);
  const [publishReceipt, setPublishReceipt] = useState<PublishResponse | null>(null);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);
  const [isRecoveredIntent, setIsRecoveredIntent] = useState(false);
  const [pendingCreatedAt, setPendingCreatedAt] = useState<number | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Global Keyboard Shortcuts (WCAG 2.2 Operable - Keyboard-First Navigation)
  // • '/' or Ctrl+K / Cmd+K: Fast jump to Agent search box
  // • 'C' or Alt+N: Open Create Agent Draft modal
  // • 'Esc' automatically closes active modal/drawer with dirty data loss prevention
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement as HTMLElement | null;
      const isInputFocused =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          activeEl.tagName === "SELECT" ||
          activeEl.isContentEditable);

      // 1. Ctrl + K or Cmd + K: Focus Search from anywhere
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchFieldRef.current?.focus();
        searchFieldRef.current?.select();
        return;
      }

      // 2. '/' shortcut: Focus Search when not typing in another input or modal
      if (
        e.key === "/" &&
        !isInputFocused &&
        !isCreateModalOpen &&
        !isEditorOpen &&
        !publishTargetAgent
      ) {
        e.preventDefault();
        searchFieldRef.current?.focus();
        searchFieldRef.current?.select();
        return;
      }

      // 3. 'C' or 'c' or Alt + N: Open Create Agent Modal
      if (
        !isCreateModalOpen &&
        !isEditorOpen &&
        !publishTargetAgent &&
        (((e.key === "c" || e.key === "C") &&
          !e.ctrlKey &&
          !e.metaKey &&
          !e.altKey &&
          !isInputFocused) ||
          (e.altKey && e.key.toLowerCase() === "n"))
      ) {
        e.preventDefault();
        handleOpenCreateModal();
        return;
      }
    };

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [
    isCreateModalOpen,
    isEditorOpen,
    publishTargetAgent,
    handleOpenCreateModal,
  ]);

  // Notification Toast with Auto-Dismiss & Progress
  const [toast, setToast] = useState<ToastNotification | null>(null);
  const toastTimerRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback(
    (message: string, type: ToastType = "success") => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      setToast({ id: Date.now(), message, type });
      toastTimerRef.current = setTimeout(() => {
        setToast(null);
      }, 4000);
    },
    []
  );

  const dismissToast = useCallback(() => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast(null);
  }, []);

  const handleCopyText = (text: string, label: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedKey(label);
      setTimeout(() => setCopiedKey(null), 2500);
      showToast(`Đã sao chép ${label} vào clipboard.`, "info");
    }
  };

  // Request cancellation on workspace switch
  const abortControllerRef = useRef<AbortController | null>(null);

  // Fetch agents from API
  const fetchAgents = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsRefreshing(true);
    setViewState("loading");
    setErrorMessage("");
    setErrorStatus(undefined);
    setRequestId(undefined);

    const scope = requestScope.current.capture();
    try {
      const page = await defaultApiClient.listAgents(workspaceId, {
        signal: controller.signal,
      });
      if (!scope.isCurrent()) return;
      if (controller.signal.aborted) return;

      const items = page.items || [];
      setAgents(items);
      setNextCursor(page.next_cursor);
      setCurrentPage(1);
      setViewState(items.length === 0 ? "empty" : "ready");
    } catch (err: unknown) {
      if (!scope.isCurrent()) return;
      if (controller.signal.aborted) return;

      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(
              window.location.pathname
            )}`
          );
          return;
        }
        if (err.isForbidden) {
          setViewState("forbidden");
          return;
        }
        setErrorStatus(err.status);
        setRequestId(err.requestId);
        setErrorMessage(
          typeof err.body === "object" &&
            err.body !== null &&
            "detail" in err.body
            ? (err.body as { detail: { message?: string } }).detail?.message ||
                err.message
            : err.message
        );
      } else {
        setErrorMessage(
          err instanceof Error
            ? err.message
            : "Không thể kết nối tới máy chủ Control Plane."
        );
      }
      setAgents([]);
      setViewState("error");
    } finally {
      if (scope.isCurrent() && !controller.signal.aborted)
        setIsRefreshing(false);
    }
  }, [workspaceId, router]);

  // Handle workspace change: reset state and fetch fresh
  useEffect(() => {
    setAgents([]);
    setIsCreateModalOpen(false);
    setIsSubmitting(false);
    setIsSaving(false);
    publishScope.current.invalidate();
    setPublishTargetAgent(null);
    setIsPublishing(false);
    setPublishStep(1);
    setPublishReceipt(null);
    setPublishMessage(null);
    setIsRecoveredIntent(false);
    setPendingCreatedAt(null);
    dismissToast();
    setCurrentPage(1);
    setNextCursor(null);
    setIsLoadingMore(false);
    setActiveDraft(null);
    setIsEditorOpen(false);
    setActiveWorkshopTab("persona");
    setConflictState(null);
    setSaveError(null);
    setCreateError("");
    fetchAgents();

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, [fetchAgents, workspaceId, dismissToast]);

  const loadMoreAgents = async () => {
    if (!nextCursor || isLoadingMore) return;
    const scope = requestScope.current.capture();
    setIsLoadingMore(true);
    try {
      const page = await defaultApiClient.listAgents(workspaceId, {
        cursor: nextCursor,
        signal: scope.signal,
      });
      if (!scope.isCurrent()) return;
      setAgents((previous) => {
        const known = new Set(previous.map((agent) => agent.agent_id));
        return [
          ...previous,
          ...page.items.filter((agent) => !known.has(agent.agent_id)),
        ];
      });
      setNextCursor(page.next_cursor);
    } catch {
      if (scope.isCurrent())
        showToast("Không thể tải thêm tác tử. Hãy thử lại.", "error");
    } finally {
      if (scope.isCurrent()) setIsLoadingMore(false);
    }
  };

  // Slug generator
  const handleNameChange = (val: string) => {
    setNewAgentName(val);
    const slugified = val
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
    setNewAgentSlug(slugified);
  };

  // Apply Starter Template
  const handleApplyTemplate = (template: StarterTemplate) => {
    setNewAgentName(template.name);
    setNewAgentSlug(template.slug);
    setNewInstructions(template.instructions);
    setNewModelRef(template.modelRef);
    setNewMode(template.mode);
    setNewMaxSteps(template.maxSteps);
    setNewMaxWallTime(template.maxWallTime);
    setNewMaxTokens(template.maxTokens);
    setCreateError("");
    setIsCreateModalOpen(true);
    showToast(
      `Đã áp dụng mẫu "${template.name}". Hãy kiểm tra và nhấn Tạo bản nháp.`,
      "info"
    );
  };

  // Submit Create Agent Draft
  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = newAgentName.trim();
    const trimmedSlug = newAgentSlug.trim();
    const trimmedInstructions = newInstructions.trim();
    const trimmedModelRef = newModelRef.trim();

    if (!trimmedName) {
      setCreateError("Vui lòng nhập tên hiển thị của Tác tử AI.");
      return;
    }
    if (!trimmedSlug || !/^[a-z0-9_-]+$/.test(trimmedSlug)) {
      setCreateError(
        "Định danh Slug chỉ được chứa chữ thường, số, gạch ngang và gạch dưới."
      );
      return;
    }
    if (!trimmedInstructions) {
      setCreateError(
        "Vui lòng nhập chỉ lệnh Persona (Persona Instructions) cho Agent."
      );
      return;
    }
    if (!trimmedModelRef) {
      setCreateError("Vui lòng nhập tham chiếu Mô hình (Model Reference).");
      return;
    }

    setIsSubmitting(true);
    setCreateError("");

    const fullSpec = {
      schemaVersion: "1.0",
      kind: "Agent",
      metadata: {
        name: trimmedSlug,
        displayName: trimmedName,
      },
      mode: newMode,
      modelRef: trimmedModelRef,
      persona: {
        instructions: trimmedInstructions,
      },
      toolRefs: [],
      skillRefs: [],
      knowledgeRefs: [],
      limits: {
        maxSteps: Number(newMaxSteps),
        maxWallTimeSeconds: Number(newMaxWallTime),
        maxTotalTokens: Number(newMaxTokens),
      },
    };

    const scope = requestScope.current.capture();
    try {
      const created = await defaultApiClient.createAgentDraft(
        workspaceId,
        {
          display_name: trimmedName,
          slug: trimmedSlug,
          spec: fullSpec,
        },
        scope.signal
      );
      if (!scope.isCurrent()) return;

      const meta: AgentDraftMetadata = {
        agent_id: created.agent_id,
        slug: created.slug,
        display_name: created.display_name,
        revision: created.revision,
        archived_at: created.archived_at,
        created_at: created.created_at,
      };

      setAgents((prev) => [meta, ...prev]);
      setIsCreateModalOpen(false);
      setNewAgentName("");
      setNewAgentSlug("");
      setNewInstructions("");
      showToast(
        `Đã tạo bản nháp Tác tử "${meta.display_name}" thành công (revision ${meta.revision}).`,
        "success"
      );
      if (viewState === "empty") setViewState("ready");
    } catch (err: unknown) {
      if (!scope.isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(
              window.location.pathname
            )}`
          );
          return;
        }
        if (err.status === 409) {
          setCreateError(
            `Định danh slug "${trimmedSlug}" đã tồn tại trong Workspace này.`
          );
          return;
        }
        if (err.status === 403) {
          setCreateError(
            "Từ chối truy cập: Bạn không có quyền khởi tạo Tác tử (cần vai trò Developer, Admin, hoặc Owner)."
          );
          return;
        }
        if (err.status === 422) {
          const detail =
            typeof err.body === "object" &&
            err.body !== null &&
            "detail" in err.body
              ? (err.body as { detail: { message?: string } }).detail?.message
              : undefined;
          setCreateError(
            `Dữ liệu không hợp lệ: ${
              detail || "AgentSpec không đúng định dạng chuẩn."
            }`
          );
          return;
        }
        setCreateError(err.message || "Lỗi máy chủ khi tạo bản nháp Agent.");
      } else {
        setCreateError(
          err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định."
        );
      }
    } finally {
      if (scope.isCurrent()) setIsSubmitting(false);
    }
  };

  // Open Editor
  const handleOpenEditor = async (agent: AgentDraftMetadata) => {
    editorScope.current.reset();
    invalidateValidation();
    setSaveError(null);
    setConflictState(null);
    setIsSaving(false);
    setValidationResult(null);
    setValidationError(null);
    setHasEditsSinceValidation(false);
    setIsValidating(false);
    setActiveWorkshopTab("persona");

    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const draft = await defaultApiClient.getAgentDraft(
        workspaceId,
        agent.agent_id,
        editor.signal
      );
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      setActiveDraft(draft);
      const spec = draft.spec || {};
      const persona = (spec.persona as { instructions?: string }) || {};
      const limits =
        (spec.limits as {
          maxSteps?: number;
          maxWallTimeSeconds?: number;
          maxTotalTokens?: number;
        }) || {};

      setEditorInstructions(persona.instructions || "");
      setEditorModelRef(String(spec.modelRef || ""));
      setEditorMode(
        (spec.mode as ExecutionMode) || "fixed_plan"
      );
      setEditorMaxSteps(Number(limits.maxSteps || 10));
      setEditorMaxWallTime(Number(limits.maxWallTimeSeconds || 180));
      setEditorMaxTokens(Number(limits.maxTotalTokens || 16000));
      setIsEditorOpen(true);
    } catch (err: unknown) {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      if (err instanceof ApiClientError && err.status === 404) {
        showToast("Tác tử không tồn tại trong Workspace này.", "error");
        fetchAgents();
        return;
      }
      showToast("Không thể tải chi tiết bản nháp Agent.", "error");
    }
  };

  // Reload latest draft from server after 412 conflict
  const handleReloadLatestDraft = async () => {
    if (!activeDraft) return;
    invalidateValidation();
    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const freshDraft = await defaultApiClient.getAgentDraft(
        workspaceId,
        activeDraft.agent_id,
        editor.signal
      );
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      setActiveDraft(freshDraft);
      const spec = freshDraft.spec || {};
      const persona = (spec.persona as { instructions?: string }) || {};
      const limits =
        (spec.limits as {
          maxSteps?: number;
          maxWallTimeSeconds?: number;
          maxTotalTokens?: number;
        }) || {};

      setEditorInstructions(persona.instructions || "");
      setEditorModelRef(String(spec.modelRef || ""));
      setEditorMode(
        (spec.mode as ExecutionMode) || "fixed_plan"
      );
      setEditorMaxSteps(Number(limits.maxSteps || 10));
      setEditorMaxWallTime(Number(limits.maxWallTimeSeconds || 180));
      setEditorMaxTokens(Number(limits.maxTotalTokens || 16000));

      setConflictState(null);
      setSaveError(null);
      showToast(
        `Đã đồng bộ bản nháp mới nhất (revision ${freshDraft.revision}) từ máy chủ.`,
        "info"
      );
    } catch {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      showToast("Không thể tải lại bản nháp từ máy chủ.", "error");
    }
  };

  // Force Save With Latest Revision (CAS Overwrite Strategy)
  const handleForceSaveWithLatestRevision = async () => {
    if (!activeDraft || !conflictState?.serverRevision) return;
    const targetRev = conflictState.serverRevision;
    setConflictState(null);
    setActiveDraft((prev) => (prev ? { ...prev, revision: targetRev } : null));

    setTimeout(() => {
      const formEl = document.getElementById(
        "edit-agent-form"
      ) as HTMLFormElement | null;
      if (formEl) formEl.requestSubmit();
    }, 50);
  };

  // Validate Agent Draft Spec (04-B)
  const handleValidateDraft = async () => {
    if (!activeDraft) return;
    setIsValidating(true);
    setValidationError(null);
    setValidationResult(null);

    const trimmedInstructions = editorInstructions.trim();
    const updatedSpec = mergeAgentDraftEdits(activeDraft.spec, {
      mode: editorMode,
      modelRef: editorModelRef.trim(),
      instructions: trimmedInstructions,
      maxSteps: Number(editorMaxSteps),
      maxWallTimeSeconds: Number(editorMaxWallTime),
      maxTotalTokens: Number(editorMaxTokens),
    });

    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    const validation = validationScope.current.begin(
      activeDraft.agent_id,
      activeDraft.revision,
      updatedSpec
    );
    try {
      const result = await defaultApiClient.validateAgentDraft(
        workspaceId,
        activeDraft.agent_id,
        { spec: updatedSpec },
        validation.signal
      );
      if (
        !scope.isCurrent() ||
        !editor.isCurrent() ||
        !validation.isCurrent()
      )
        return;
      if (!validation.matchesResponse(result)) {
        throw new Error(
          "Kết quả kiểm tra không khớp Agent hoặc revision hiện tại. Hãy tải lại bản nháp."
        );
      }
      setValidationResult(result);
      setHasEditsSinceValidation(false);
      if (result.ready) {
        showToast("Cấu hình hợp lệ và sẵn sàng công bố.", "success");
      } else {
        showToast(
          "Cấu hình chưa sẵn sàng (có phát hiện lỗi phụ thuộc).",
          "error"
        );
      }
    } catch (err: unknown) {
      if (
        !scope.isCurrent() ||
        !editor.isCurrent() ||
        !validation.isCurrent()
      )
        return;
      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(
              window.location.pathname
            )}`
          );
          return;
        }
        setValidationError(err.message || "Lỗi kiểm tra cấu hình.");
      } else {
        setValidationError(
          err instanceof Error
            ? err.message
            : "Lỗi không xác định khi kiểm tra cấu hình."
        );
      }
    } finally {
      if (scope.isCurrent() && editor.isCurrent() && validation.isCurrent())
        setIsValidating(false);
    }
  };

  // Submit Save Agent Draft Spec (PATCH with If-Match)
  const handleSaveDraftSpec = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeDraft) return;
    invalidateValidation();

    const trimmedInstructions = editorInstructions.trim();
    if (!trimmedInstructions) {
      setSaveError("Chỉ lệnh Persona không được để trống.");
      setActiveWorkshopTab("persona");
      return;
    }

    setIsSaving(true);
    setSaveError(null);

    const updatedSpec = mergeAgentDraftEdits(activeDraft.spec, {
      mode: editorMode,
      modelRef: editorModelRef.trim(),
      instructions: trimmedInstructions,
      maxSteps: Number(editorMaxSteps),
      maxWallTimeSeconds: Number(editorMaxWallTime),
      maxTotalTokens: Number(editorMaxTokens),
    });

    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const saved = await defaultApiClient.replaceAgentDraftSpec(
        workspaceId,
        activeDraft.agent_id,
        { spec: updatedSpec },
        activeDraft.revision,
        editor.signal
      );
      if (!scope.isCurrent() || !editor.isCurrent()) return;

      setActiveDraft(saved);
      setConflictState(null);
      setSaveError(null);
      invalidateValidation();

      setAgents((prev) =>
        prev.map((ag) =>
          ag.agent_id === saved.agent_id
            ? { ...ag, revision: saved.revision }
            : ag
        )
      );

      showToast(
        `Đã lưu thay đổi thành công (revision ${saved.revision}).`,
        "success"
      );
    } catch (err: unknown) {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          let serverDraftData: AgentDraft | null = null;
          try {
            serverDraftData = await defaultApiClient.getAgentDraft(
              workspaceId,
              activeDraft.agent_id,
              editor.signal
            );
          } catch {
            // fallback
          }

          setConflictState({
            message:
              "Xung đột sửa đổi (HTTP 412 Stale Revision): Bản nháp đã được cập nhật bởi một phiên làm việc khác.",
            serverRevision: serverDraftData
              ? serverDraftData.revision
              : (activeDraft.revision || 1) + 1,
            serverDraft: serverDraftData,
          });
          return;
        }
        if (err.status === 409) {
          setSaveError(
            "Bản nháp này đã bị lưu trữ (archived) hoặc xảy ra xung đột tài nguyên."
          );
          return;
        }
        if (err.status === 403) {
          setSaveError(
            "Từ chối truy cập: Bạn không có quyền chỉnh sửa Tác tử này."
          );
          return;
        }
        if (err.status === 422) {
          const detail =
            typeof err.body === "object" &&
            err.body !== null &&
            "detail" in err.body
              ? (err.body as { detail: { message?: string } }).detail?.message
              : undefined;
          setSaveError(
            `Cấu trúc spec không hợp lệ: ${
              detail || "Lỗi kiểm tra tính hợp lệ của schema."
            }`
          );
          return;
        }
        setSaveError(err.message || "Lỗi máy chủ khi cập nhật bản nháp.");
      } else {
        setSaveError(
          err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định."
        );
      }
    } finally {
      if (scope.isCurrent() && editor.isCurrent()) setIsSaving(false);
    }
  };

  // Close Publish Modal
  const closePublishModal = useCallback(() => {
    publishScope.current.invalidate();
    setPublishTargetAgent(null);
    setPublishMessage(null);
    setIsPublishing(false);
    setPublishStep(1);
    setPublishReceipt(null);
    setIsRecoveredIntent(false);
    setPendingCreatedAt(null);
  }, []);

  const handleClosePublishModal = useCallback(() => {
    if (isPublishing) {
      const confirmed = window.confirm(
        "Giao dịch xuất bản đang được xử lý trên máy chủ. Bạn có chắc chắn muốn đóng giao diện này?"
      );
      if (!confirmed) return;
    }
    closePublishModal();
  }, [isPublishing, closePublishModal]);

  // Create Fresh Publish Intent
  const handleCreateFreshIntent = () => {
    if (!publishTargetAgent) return;
    clearPendingPublishIntent(workspaceId, publishTargetAgent.agent_id);
    const freshKey =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `pub-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
    setPublishIdempotencyKey(freshKey);
    setPublishRevision(publishTargetAgent.revision);
    setPendingCreatedAt(null);
    setIsRecoveredIntent(false);
    setPublishMessage(null);
    showToast("Đã hủy ý định cũ và tạo khóa xuất bản mới.", "info");
  };

  // Open Publish Modal
  const handleOpenPublishModal = (agent: AgentDraftMetadata) => {
    setPublishMessage(null);
    setPublishReceipt(null);
    setPublishStep(1);
    setPublishTargetAgent(agent);

    const pending = getPendingPublishIntent(workspaceId, agent.agent_id);
    if (pending && pending.status === "pending") {
      setPublishIdempotencyKey(pending.operationKey);
      setPublishRevision(pending.expectedRevision);
      setPendingCreatedAt(pending.createdAt || Date.now());
      setIsRecoveredIntent(true);
      setPublishMessage(null);
    } else {
      const freshKey =
        typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : `pub-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
      setPublishIdempotencyKey(freshKey);
      setPublishRevision(agent.revision);
      setPendingCreatedAt(null);
      setIsRecoveredIntent(false);
    }
  };

  // Submit Publish Agent (R04C-04, R04C-05, Stepper + Certificate)
  const handleConfirmPublish = async () => {
    if (!publishTargetAgent) return;

    const targetAgent = publishTargetAgent;
    const targetAgentId = targetAgent.agent_id;
    const targetRevision = publishRevision;
    const targetKey = publishIdempotencyKey;

    const publishOp = publishScope.current.begin(
      workspaceId,
      targetAgentId,
      targetKey,
      targetRevision
    );

    savePendingPublishIntent({
      workspaceId,
      agentId: targetAgentId,
      operationKey: targetKey,
      expectedRevision: targetRevision,
      status: "pending",
      createdAt: Date.now(),
    });

    setIsPublishing(true);
    setPublishStep(1);
    setPublishMessage(null);
    setPublishReceipt(null);

    const stepTimer1 = setTimeout(() => {
      setPublishStep(2);
    }, 450);
    const stepTimer2 = setTimeout(() => {
      setPublishStep(3);
    }, 1000);

    const workspaceSignal = requestScope.current.capture();
    try {
      const res = await defaultApiClient.publishAgent(
        workspaceId,
        targetAgentId,
        {
          expectedRevision: targetRevision,
          idempotencyKey: targetKey,
        },
        publishOp.signal
      );

      clearTimeout(stepTimer1);
      clearTimeout(stepTimer2);

      if (!workspaceSignal.isCurrent() || !publishOp.isCurrent()) return;
      if (!publishOp.matchesResponse(res)) return;

      clearPendingPublishIntent(workspaceId, targetAgentId);

      setPublishReceipt(res);
      setPublishStep(3);
      setIsPublishing(false);
      showToast(
        `Đã xuất bản Tác tử "${targetAgent.display_name}" thành phiên bản ${res.version}.`,
        "success"
      );
      await fetchAgents();
    } catch (err: unknown) {
      clearTimeout(stepTimer1);
      clearTimeout(stepTimer2);
      if (!workspaceSignal.isCurrent() || !publishOp.isCurrent()) return;

      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          clearPendingPublishIntent(workspaceId, targetAgentId);
          setPublishMessage(
            "Bản nháp đã bị thay đổi trước khi xuất bản. Vui lòng đóng hộp thoại và tải lại trang."
          );
          return;
        }
        if (err.status === 422) {
          clearPendingPublishIntent(workspaceId, targetAgentId);
          setPublishMessage(
            "Tác tử chưa sẵn sàng xuất bản: phụ thuộc hoặc cấu hình không hợp lệ. Vui lòng kiểm tra lại chẩn đoán."
          );
          return;
        }
        if (err.status === 403) {
          clearPendingPublishIntent(workspaceId, targetAgentId);
          setPublishMessage(
            "Từ chối truy cập: Bạn không có quyền xuất bản Tác tử."
          );
          return;
        }
        if (err.status === 409) {
          setPublishMessage(
            "Xung đột xuất bản: Khóa idempotency đã được dùng với yêu cầu khác hoặc đang được xử lý đồng thời."
          );
          return;
        }
        setPublishMessage(
          err.message || "Dịch vụ xuất bản tạm thời không khả dụng."
        );
      } else {
        setPublishMessage(
          err instanceof Error
            ? err.message
            : "Dịch vụ xuất bản tạm thời không khả dụng."
        );
      }
    } finally {
      if (workspaceSignal.isCurrent() && publishOp.isCurrent()) {
        setIsPublishing(false);
      }
    }
  };

  // Filtered & Paginated items
  const filteredAgents = useMemo(() => {
    return agents.filter((item) => {
      const matchQuery =
        item.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.slug.toLowerCase().includes(searchQuery.toLowerCase());

      const matchStatus =
        statusFilter === "ALL" ||
        (statusFilter === "DRAFT" && !item.archived_at) ||
        (statusFilter === "ARCHIVED" && Boolean(item.archived_at));

      return matchQuery && matchStatus;
    });
  }, [agents, searchQuery, statusFilter]);

  const totalPages = Math.max(
    1,
    Math.ceil(filteredAgents.length / itemsPerPage)
  );
  const paginatedAgents = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return filteredAgents.slice(start, start + itemsPerPage);
  }, [filteredAgents, currentPage, itemsPerPage]);

  const isFilteringActive =
    searchQuery.trim() !== "" ||
    statusFilter !== "ALL" ||
    modeFilter !== "ALL" ||
    providerFilter !== "ALL";

  const handleResetFilters = () => {
    setSearchQuery("");
    setStatusFilter("ALL");
    setModeFilter("ALL");
    setProviderFilter("ALL");
  };

  // Format date helper
  const formatDate = (isoString?: string | null) => {
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
  };

  // Line count and Prompt security metrics
  const lineNumbers = useMemo(() => {
    const count = Math.max(1, editorInstructions.split("\n").length);
    return Array.from({ length: count }, (_, i) => i + 1);
  }, [editorInstructions]);

  const tokenEstimate = useMemo(() => {
    return estimateTokenCount(editorInstructions);
  }, [editorInstructions]);

  const promptSafety = useMemo(() => {
    return detectPromptSafetyRisk(editorInstructions);
  }, [editorInstructions]);

  // Diff comparison rows for Conflict Preview
  const conflictDiffs = useMemo(() => {
    if (!conflictState) return [];
    const serverSpec = conflictState.serverDraft?.spec ?? null;
    return compareDraftStates(serverSpec, {
      mode: editorMode,
      modelRef: editorModelRef,
      instructions: editorInstructions,
      maxSteps: editorMaxSteps,
      maxWallTimeSeconds: editorMaxWallTime,
      maxTotalTokens: editorMaxTokens,
    });
  }, [
    conflictState,
    editorMode,
    editorModelRef,
    editorInstructions,
    editorMaxSteps,
    editorMaxWallTime,
    editorMaxTokens,
  ]);

  // Dependencies extracted from activeDraft.spec
  const activeDependencies = useMemo(() => {
    if (!activeDraft?.spec) return { tools: [], skills: [], knowledge: [] };
    const spec = activeDraft.spec;
    const tools = Array.isArray(spec.toolRefs) ? spec.toolRefs : [];
    const skills = Array.isArray(spec.skillRefs) ? spec.skillRefs : [];
    const knowledge = Array.isArray(spec.knowledgeRefs)
      ? spec.knowledgeRefs
      : [];
    return { tools, skills, knowledge };
  }, [activeDraft]);

  // Table Columns Definition
  const columns: Column<AgentDraftMetadata>[] = [
    {
      key: "display_name",
      header: "AI Agent & Identity",
      width: "42%",
      render: (item) => (
        <div className="agent-identity-cell">
          <AgentIdenticon
            slug={item.slug}
            name={item.display_name}
            size="md"
          />
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
      render: (item) => (
        <span className="agent-rev-token">rev {item.revision}</span>
      ),
    },
    {
      key: "status",
      header: "Lifecycle Status",
      width: "16%",
      render: (item) => (
        <AgentStatusPill
          archived={Boolean(item.archived_at)}
          revision={item.revision}
        />
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
              handleOpenEditor(item);
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
              handleOpenPublishModal(item);
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
  ];

  return (
    <div className="agents-page-root">
      {/* Toast Notification with Progress Bar & Dismiss */}
      {toast && (
        <div
          role={toast.type === "error" ? "alert" : "status"}
          aria-live={toast.type === "error" ? "assertive" : "polite"}
          aria-atomic="true"
          className={`agent-toast-card toast-${toast.type}`}
        >
          <div className="toast-content-wrapper">
            {toast.type === "success" && (
              <svg
                aria-hidden="true"
                className="toast-icon icon-success"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            )}
            {toast.type === "error" && (
              <svg
                aria-hidden="true"
                className="toast-icon icon-error"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            )}
            {toast.type === "info" && (
              <svg
                aria-hidden="true"
                className="toast-icon icon-info"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="8" x2="12.01" y2="8" />
              </svg>
            )}
            <span className="toast-text">{toast.message}</span>
            <button
              type="button"
              className="toast-dismiss-button"
              onClick={dismissToast}
              aria-label="Đóng thông báo"
            >
              ✕
            </button>
          </div>
          <div className="toast-progress-bar" />
        </div>
      )}

      {/* Page Header */}
      <PageHeader
        title="AI Agents"
        description="Quản lý vòng đời tác tử AI doanh nghiệp: khởi tạo bản nháp, tinh chỉnh chỉ lệnh Persona, và kiểm soát tương tranh lạc quan."
        breadcrumbs={[
          { label: "Workspace", href: `/w/${workspaceId}/overview` },
          { label: "AI Agents", isCurrent: true },
        ]}
        badge={
          <StatusBadge
            status="DRAFT"
            customLabel={`${agents.length} AGENTS`}
            size="sm"
          />
        }
        actions={
          <Button
            ref={createButtonRef}
            variant="primary"
            size="md"
            onClick={handleOpenCreateModal}
            aria-label="Khởi tạo bản nháp Tác tử AI mới (Nhấn C hoặc Alt+N)"
            title="Khởi tạo bản nháp Tác tử AI mới (Phím tắt: C hoặc Alt+N)"
            leftIcon={
              <svg
                aria-hidden="true"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            }
          >
            + Create Agent
          </Button>
        }
      />

      {/* Stale Banner */}
      {viewState === "stale" && (
        <StaleBanner
          message="Dữ liệu hiển thị có thể chưa đồng bộ do có sửa đổi phiên bản mới hơn trên máy chủ khác."
          onRefresh={fetchAgents}
          isRefreshing={isRefreshing}
        />
      )}

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
                onChange={(e) => setSearchQuery(e.target.value)}
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
                    setSearchQuery("");
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
              {`Đang hiển thị ${filteredAgents.length} trên tổng số ${agents.length} tác tử.`}
            </div>

            {/* Quick Status Dropdown */}
            <label htmlFor="agent-status-filter" className="visually-hidden">
              Lọc theo trạng thái tác tử
            </label>
            <select
              id="agent-status-filter"
              className="toolbar-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
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
              onClick={fetchAgents}
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
                onClick={handleResetFilters}
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
                className={`view-mode-button ${
                  viewMode === "table" ? "active" : ""
                }`}
                onClick={() => setViewMode("table")}
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
                className={`view-mode-button ${
                  viewMode === "grid" ? "active" : ""
                }`}
                onClick={() => setViewMode("grid")}
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

      {/* 6 UI States Rendering */}
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
          message={
            errorMessage ||
            "Đã xảy ra lỗi khi kết nối tới máy chủ Control Plane."
          }
          statusCode={errorStatus}
          requestId={requestId}
          onRetry={fetchAgents}
          isRetrying={isRefreshing}
        />
      )}

      {viewState === "forbidden" && (
        <ForbiddenState
          title="Truy cập bị từ chối"
          message="Tài khoản của bạn không có vai trò 'agent:read' trong Không gian làm việc này."
          onGoBack={() => setViewState("ready")}
          onContactAdmin={() =>
            showToast(
              "Đã gửi thông báo yêu cầu cấp quyền đến Quản trị viên.",
              "info"
            )
          }
        />
      )}

      {/* Empty State with Enterprise Starter Templates */}
      {viewState === "empty" && (
        <div className="agents-empty-wrapper">
          <EmptyState
            title="Chưa có Tác tử AI nào trong không gian này"
            description="Bắt đầu bằng cách tạo bản nháp tác tử đầu tiên hoặc chọn nhanh một trong các mẫu chuẩn doanh nghiệp dưới đây."
            actionLabel="+ Khởi tạo Tác tử Mới"
            onAction={() => {
              setCreateError("");
              setIsCreateModalOpen(true);
            }}
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
                  onClick={() => handleApplyTemplate(tmpl)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      handleApplyTemplate(tmpl);
                    }
                  }}
                >
                  <div className="template-card-header">
                    <AgentIdenticon
                      slug={tmpl.slug}
                      name={tmpl.name}
                      size="sm"
                    />
                    <span className="template-mode-pill">{tmpl.badge}</span>
                  </div>
                  <h4 className="template-card-title">{tmpl.name}</h4>
                  <p className="template-card-desc">{tmpl.description}</p>
                  <div className="template-card-footer">
                    <span className="template-apply-action">
                      Áp dụng mẫu này →
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Ready / Stale View */}
      {(viewState === "ready" || viewState === "stale") && (
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
                    description={`Không có tác tử nào khớp với từ khóa "${searchQuery}". Xóa bộ lọc tìm kiếm để xem tất cả.`}
                    actionLabel="Xóa bộ lọc"
                    onAction={handleResetFilters}
                  />
                }
              />
            </div>
          ) : (
            <div className="agent-grid-cards-container">
              {paginatedAgents.length === 0 ? (
                <EmptyState
                  title="Không tìm thấy tác tử phù hợp"
                  description={`Không có tác tử nào khớp với từ khóa "${searchQuery}". Xóa bộ lọc tìm kiếm để xem tất cả.`}
                  actionLabel="Xóa bộ lọc"
                  onAction={handleResetFilters}
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
                            <h3
                              className="card-agent-name"
                              title={agent.display_name}
                            >
                              {agent.display_name}
                            </h3>
                            <span className="card-agent-slug">
                              {agent.slug}
                            </span>
                          </div>
                        </div>

                        <div className="card-meta-section">
                          <div className="card-status-badges">
                            <AgentStatusPill
                              archived={isArchived}
                              revision={agent.revision}
                            />
                            <span className="card-rev-badge">
                              rev {agent.revision}
                            </span>
                          </div>

                          <div className="card-date-info">
                            <svg
                              width="12"
                              height="12"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
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
                            onClick={() => handleOpenEditor(agent)}
                            title={`Chỉnh sửa bản nháp tác tử ${agent.display_name}`}
                            aria-label={`Chỉnh sửa bản nháp tác tử ${agent.display_name}`}
                          >
                            Chỉnh sửa
                          </Button>
                          <Button
                            variant="copper"
                            size="sm"
                            disabled={isArchived}
                            onClick={() => handleOpenPublishModal(agent)}
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
                            <BrandIcon
                              name="action-publish"
                              size={13}
                              color="currentColor"
                            />
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
            onPageChange={setCurrentPage}
            totalItems={filteredAgents.length}
            itemsPerPage={itemsPerPage}
          />

          {nextCursor && (
            <div className="load-more-container">
              <Button disabled={isLoadingMore} onClick={loadMoreAgents}>
                {isLoadingMore ? "Đang tải…" : "Tải thêm tác tử"}
              </Button>
            </div>
          )}
        </>
      )}

      {/* Modal: Create Agent Draft */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={handleCloseCreateModal}
        title="Tạo Bản nháp Tác tử AI (Create Agent Draft)"
        description="Khởi tạo bản nháp với cấu hình AgentSpec đầy đủ. Ràng buộc mô hình và công cụ sẽ được xác thực tại Bước 04-B."
        maxWidth="560px"
        footer={
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={handleCloseCreateModal}
              disabled={isSubmitting}
            >
              Hủy
            </Button>
            <Button
              variant="primary"
              size="md"
              type="submit"
              form="create-agent-form"
              isLoading={isSubmitting}
            >
              Tạo bản nháp
            </Button>
          </>
        }
      >
        <form
          id="create-agent-form"
          onSubmit={handleCreateAgent}
          className="modal-form-vertical"
        >
          <Input
            id="new-agent-name"
            label="Tên hiển thị Agent (Display Name) *"
            placeholder="Ví dụ: Trợ lý Soạn thảo Báo giá"
            value={newAgentName}
            onChange={(e) => handleNameChange(e.target.value)}
            required
            autoFocus
          />

          <Input
            id="new-agent-slug"
            label="Định danh Kỹ thuật (Technical Slug) *"
            placeholder="tro-ly-soan-thao-bao-gia"
            value={newAgentSlug}
            onChange={(e) => setNewAgentSlug(e.target.value)}
            helperText="Chữ thường không dấu, số và gạch ngang (bất biến sau khi tạo)."
            required
          />

          <div className="form-field-group">
            <label htmlFor="agent-instructions" className="form-field-label">
              Chỉ lệnh Persona (Instructions) *
            </label>
            <textarea
              id="agent-instructions"
              rows={4}
              placeholder="Nhập chỉ lệnh hoạt động, mục tiêu và ranh giới an toàn cho tác tử..."
              value={newInstructions}
              onChange={(e) => setNewInstructions(e.target.value)}
              required
              aria-required="true"
              className="form-textarea-control"
            />
          </div>

          <Input
            id="new-model-ref"
            label="Tham chiếu Mô hình (Model Reference) *"
            placeholder="approved-general@2"
            value={newModelRef}
            onChange={(e) => setNewModelRef(e.target.value)}
            helperText="Tham chiếu định danh cấu hình Model (sẽ được đối soát ở Bước 04-B)."
            required
          />

          <div className="form-row-2col">
            <div className="form-field-group">
              <label htmlFor="agent-mode" className="form-field-label">
                Chế độ thực thi (Mode)
              </label>
              <select
                id="agent-mode"
                value={newMode}
                onChange={(e) =>
                  setNewMode(e.target.value as ExecutionMode)
                }
                className="form-select-control"
              >
                <option value="fixed_plan">fixed_plan</option>
                <option value="planner_based">planner_based</option>
                <option value="react">react</option>
              </select>
            </div>

            <Input
              id="new-max-steps"
              label="Số bước tối đa (Max Steps) *"
              type="number"
              min={1}
              max={50}
              value={newMaxSteps}
              onChange={(e) => setNewMaxSteps(Number(e.target.value))}
              required
            />
          </div>

          {createError && (
            <div className="form-error-banner" role="alert" aria-live="assertive">
              <span aria-hidden="true">✕</span> {createError}
            </div>
          )}
        </form>
      </Modal>

      {/* Modal: AGENT STUDIO WORKSHOP (Tabbed / Split-view / Diagnostics / Diff OCC) */}
      <Modal
        isOpen={isEditorOpen}
        onClose={handleCloseEditor}
        title={`Agent Studio Workshop: ${activeDraft?.display_name || ""}`}
        description={`Định danh: ${activeDraft?.slug} | Bản nháp máy chủ: revision ${activeDraft?.revision}`}
        maxWidth="920px"
        footer={
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={handleCloseEditor}
              disabled={isSaving || isValidating}
            >
              Đóng Workshop
            </Button>
            <Button
              variant="outline"
              size="md"
              type="button"
              onClick={handleValidateDraft}
              isLoading={isValidating}
              disabled={isSaving}
              title="Đối soát ràng buộc và dependencies tại Step 04-B"
              aria-label="Đối soát ràng buộc và dependencies tại Step 04-B"
            >
              Kiểm tra cấu hình (Validate)
            </Button>
            <Button
              variant="primary"
              size="md"
              type="submit"
              form="edit-agent-form"
              isLoading={isSaving}
              disabled={isValidating}
              aria-label="Lưu các thay đổi cấu hình AgentSpec"
            >
              Lưu thay đổi (Save Spec)
            </Button>
          </>
        }
      >
        <div className="workshop-container">
          {/* Interactive Diagnostic Center (04-B) */}
          {validationError && (
            <div className="form-error-banner" role="alert" aria-live="assertive">
              <span aria-hidden="true">✕</span> {validationError}
            </div>
          )}

          {validationResult && (
            <div
              className={`diagnostic-center-box ${
                validationReady ? "diagnostic-ready" : "diagnostic-issues"
              }`}
              role="region"
              aria-label="Trung tâm chẩn đoán cấu hình Agent"
            >
              <div className="diagnostic-header-bar">
                <div className="diagnostic-status-indicator">
                  {validationReady ? (
                    <span className="diagnostic-badge-success">
                      ✓ SẴN SÀNG CÔNG BỐ
                    </span>
                  ) : (
                    <span className="diagnostic-badge-danger">
                      ✕ CẦN XỬ LÝ {validationResult.diagnostics.length} VẤN ĐỀ
                    </span>
                  )}
                  <span className="diagnostic-rev-tag">
                    Đã kiểm chứng tại rev {validationResult.revision}
                  </span>
                  {validationIsStale && (
                    <span className="diagnostic-stale-pill">
                      ⚠️ Cấu hình đã thay đổi sau lần kiểm tra gần nhất
                    </span>
                  )}
                </div>

                <button
                  type="button"
                  className="diagnostic-recheck-btn"
                  onClick={handleValidateDraft}
                  disabled={isValidating}
                  title="Kiểm tra lại cấu hình hiện tại"
                  aria-label="Kiểm tra lại cấu hình hiện tại"
                >
                  <svg
                    aria-hidden="true"
                    className={isValidating ? "icon-spin" : ""}
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                  >
                    <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
                  </svg>
                  <span>{isValidating ? "Đang kiểm tra…" : "Kiểm tra lại"}</span>
                </button>
              </div>

              {!validationResult.ready && categorizedDiagnostics.length > 0 && (
                <div className="diagnostic-issues-grid">
                  {criticalIssuesCount > 0 && (
                    <div className="diagnostic-group-section">
                      <div className="diagnostic-group-header text-danger">
                        🔴 Lỗi Nghiêm trọng (Critical Blockers - Không thể xuất bản):
                      </div>
                      <div className="diagnostic-items-list" role="list" aria-label="Danh sách lỗi nghiêm trọng">
                        {categorizedDiagnostics
                          .filter((d) => d.severity === "critical")
                          .map((d, idx) => (
                            <div key={idx} className="diagnostic-issue-card card-critical" role="listitem">
                              <div className="issue-content">
                                <span className="issue-field">[{d.field}]</span>
                                <span className="issue-code">({d.code})</span>:{" "}
                                <span className="issue-message">{d.message}</span>
                              </div>
                              <button
                                type="button"
                                className="issue-focus-btn"
                                onClick={() => handleFocusDiagnosticField(d)}
                                aria-label={`Nhảy tới trường dữ liệu ${d.field} để sửa lỗi ${d.code}`}
                              >
                                Nhảy tới trường →
                              </button>
                            </div>
                          ))}
                      </div>
                    </div>
                  )}

                  {warningIssuesCount > 0 && (
                    <div className="diagnostic-group-section">
                      <div className="diagnostic-group-header text-warning">
                        🟡 Cảnh báo & Khuyến nghị (Warnings):
                      </div>
                      <div className="diagnostic-items-list" role="list" aria-label="Danh sách cảnh báo">
                        {categorizedDiagnostics
                          .filter((d) => d.severity === "warning")
                          .map((d, idx) => (
                            <div key={idx} className="diagnostic-issue-card card-warning" role="listitem">
                              <div className="issue-content">
                                <span className="issue-field">[{d.field}]</span>
                                <span className="issue-code">({d.code})</span>:{" "}
                                <span className="issue-message">{d.message}</span>
                              </div>
                              <button
                                type="button"
                                className="issue-focus-btn"
                                onClick={() => handleFocusDiagnosticField(d)}
                                aria-label={`Nhảy tới trường dữ liệu ${d.field} để khắc phục cảnh báo ${d.code}`}
                              >
                                Nhảy tới trường →
                              </button>
                            </div>
                          ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Side-by-Side Diff Preview for 412 Stale Revision Conflict */}
          {conflictState && (
            <div className="conflict-workbench-card" role="alert" aria-live="polite">
              <div className="conflict-workbench-header">
                <div className="conflict-icon-title">
                  <svg
                    aria-hidden="true"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--color-warning)"
                    strokeWidth="2.5"
                  >
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                    <line x1="12" y1="9" x2="12" y2="13" />
                    <line x1="12" y1="17" x2="12.01" y2="17" />
                  </svg>
                  <div>
                    <h4 className="conflict-title-text">
                      Xung đột sửa đổi tương tranh (HTTP 412 Stale Revision Conflict)
                    </h4>
                    <p className="conflict-desc-text">
                      Bản nháp trên máy chủ đã được nâng lên <strong>rev {conflictState.serverRevision}</strong>.
                      Thay đổi cục bộ của bạn được giữ lại nguyên vẹn để đối soát.
                    </p>
                  </div>
                </div>

                <div className="conflict-decision-actions">
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    onClick={handleReloadLatestDraft}
                    title="Hủy bỏ thay đổi cục bộ và tải bản máy chủ mới nhất"
                    aria-label="Áp dụng bản Server mới nhất và hủy bỏ thay đổi cục bộ"
                  >
                    Áp dụng bản Server
                  </Button>
                  <Button
                    variant="copper"
                    size="sm"
                    type="button"
                    onClick={handleForceSaveWithLatestRevision}
                    title="Ghi đè với bản của tôi dưới revision mới (Cần CAS review)"
                    aria-label="Ghi đè với bản của tôi dưới revision mới (Cần CAS review)"
                  >
                    Ghi đè với bản của tôi
                  </Button>
                </div>
              </div>

              {/* Side-by-Side Diff Table */}
              <div className="conflict-diff-table" role="table" aria-label="Bảng so sánh chi tiết giữa bản Server và bản sửa đổi cục bộ">
                <div className="diff-table-header" role="row">
                  <span className="diff-col-field" role="columnheader">Trường dữ liệu</span>
                  <span className="diff-col-server" role="columnheader">
                    Bản Server (rev {conflictState.serverRevision})
                  </span>
                  <span className="diff-col-local" role="columnheader">
                    Bản sửa đổi của bạn (rev {activeDraft?.revision})
                  </span>
                </div>
                {conflictDiffs.map((diff) => (
                  <div
                    key={diff.field}
                    className={`diff-table-row ${
                      diff.isDifferent ? "row-highlight-diff" : ""
                    }`}
                    role="row"
                  >
                    <span className="diff-cell-field" role="cell">
                      {diff.label}
                      {diff.isDifferent && (
                        <span className="diff-change-badge">Thay đổi</span>
                      )}
                    </span>
                    <span className="diff-cell-server" role="cell">
                      {diff.serverValue || "—"}
                    </span>
                    <span className="diff-cell-local" role="cell">
                      {diff.localValue || "—"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {saveError && (
            <div className="form-error-banner" role="alert" aria-live="assertive">
              <span aria-hidden="true">✕</span> {saveError}
            </div>
          )}

          {/* Workshop Tab Navigation */}
          <div
            className="workshop-nav-tabs"
            role="tablist"
            aria-label="Các phân hệ cấu hình Tác tử AI trong Workshop"
          >
            <button
              id="workshop-tab-persona"
              type="button"
              role="tab"
              aria-selected={activeWorkshopTab === "persona"}
              aria-controls="workshop-tabpanel-persona"
              tabIndex={activeWorkshopTab === "persona" ? 0 : -1}
              className={`workshop-tab-btn ${
                activeWorkshopTab === "persona" ? "tab-active" : ""
              }`}
              onClick={() => setActiveWorkshopTab("persona")}
              onKeyDown={(e) => handleTabKeyDown(e, "persona")}
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
                <path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
              </svg>
              <span>1. Persona & Prompt</span>
            </button>

            <button
              id="workshop-tab-model"
              type="button"
              role="tab"
              aria-selected={activeWorkshopTab === "model"}
              aria-controls="workshop-tabpanel-model"
              tabIndex={activeWorkshopTab === "model" ? 0 : -1}
              className={`workshop-tab-btn ${
                activeWorkshopTab === "model" ? "tab-active" : ""
              }`}
              onClick={() => setActiveWorkshopTab("model")}
              onKeyDown={(e) => handleTabKeyDown(e, "model")}
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
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              <span>2. Model & Runtime Bounds</span>
            </button>

            <button
              id="workshop-tab-dependencies"
              type="button"
              role="tab"
              aria-selected={activeWorkshopTab === "dependencies"}
              aria-controls="workshop-tabpanel-dependencies"
              tabIndex={activeWorkshopTab === "dependencies" ? 0 : -1}
              className={`workshop-tab-btn ${
                activeWorkshopTab === "dependencies" ? "tab-active" : ""
              }`}
              onClick={() => setActiveWorkshopTab("dependencies")}
              onKeyDown={(e) => handleTabKeyDown(e, "dependencies")}
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
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
              <span>
                3. Dependencies & Bindings (
                {activeDependencies.tools.length +
                  activeDependencies.skills.length +
                  activeDependencies.knowledge.length}
                )
              </span>
            </button>
          </div>

          <form
            id="edit-agent-form"
            onSubmit={handleSaveDraftSpec}
            className="workshop-form-content"
          >
            {/* TAB 1: PERSONA & PROMPT */}
            {activeWorkshopTab === "persona" && (
              <div
                id="workshop-tabpanel-persona"
                role="tabpanel"
                aria-labelledby="workshop-tab-persona"
                tabIndex={0}
                className="tab-pane-persona"
              >
                <div className="persona-meta-strip">
                  <div className="meta-info-item">
                    <span className="meta-label">Định danh Slug:</span>
                    <span className="meta-mono-val">{activeDraft?.slug}</span>
                  </div>
                  <div className="meta-info-item">
                    <span className="meta-label">Current Revision:</span>
                    <span className="agent-rev-token">
                      rev {activeDraft?.revision}
                    </span>
                  </div>
                  <div className="meta-info-item">
                    <span className="meta-label">Ước tính Token:</span>
                    <span className="meta-token-count">
                      ~{tokenEstimate} tokens ({editorInstructions.length} ký tự)
                    </span>
                  </div>
                  <div className="meta-info-item">
                    {promptSafety.hasRisk ? (
                      <span className="safety-badge-warning" title={promptSafety.reason}>
                        ⚠️ Cảnh báo Injection: {promptSafety.reason}
                      </span>
                    ) : (
                      <span className="safety-badge-safe">
                        🛡️ Persona Security: Chuẩn B2B
                      </span>
                    )}
                  </div>
                </div>

                <div className="persona-editor-wrapper">
                  {/* Line Numbers Gutter */}
                  <div
                    ref={gutterRef}
                    className="editor-line-gutter"
                    aria-hidden="true"
                  >
                    {lineNumbers.map((num) => (
                      <div key={num} className="line-num">
                        {num}
                      </div>
                    ))}
                  </div>

                  {/* Code Textarea */}
                  <textarea
                    id="edit-instructions"
                    rows={14}
                    value={editorInstructions}
                    onChange={(e) => {
                      setEditorInstructions(e.target.value);
                      invalidateValidation();
                    }}
                    onScroll={handleTextareaScroll}
                    required
                    aria-required="true"
                    aria-label="Chỉ lệnh hoạt động Persona (Instructions)"
                    aria-describedby="persona-editor-hint"
                    className="editor-textarea-mono"
                    placeholder="Nhập chỉ lệnh hoạt động, mục tiêu Persona, và ranh giới an toàn cho tác tử AI..."
                  />
                </div>

                <div className="persona-editor-footer" id="persona-editor-hint">
                  <span className="editor-hint-text">
                    💡 <strong>Mẹo kiến trúc Persona:</strong> Định nghĩa rõ ràng
                    vai trò, ngữ cảnh doanh nghiệp, giới hạn phạm vi gọi công cụ
                    và định dạng phản hồi đầu ra (JSON/Markdown).
                  </span>
                </div>
              </div>
            )}

            {/* TAB 2: MODEL & RUNTIME BOUNDS */}
            {activeWorkshopTab === "model" && (
              <div
                id="workshop-tabpanel-model"
                role="tabpanel"
                aria-labelledby="workshop-tab-model"
                tabIndex={0}
                className="tab-pane-model"
              >
                {/* Model Reference & Mode */}
                <div className="model-selection-grid">
                  <div className="form-field-group">
                    <label htmlFor="edit-model-ref" className="form-field-label">
                      Tham chiếu Mô hình (Model Reference) *
                    </label>
                    <input
                      id="edit-model-ref"
                      type="text"
                      className="form-control-input"
                      value={editorModelRef}
                      onChange={(e) => {
                        setEditorModelRef(e.target.value);
                        invalidateValidation();
                      }}
                      placeholder="approved-general@2"
                      required
                      aria-required="true"
                      aria-label="Tham chiếu định danh cấu hình Model"
                    />
                    <div className="quick-model-chips" role="group" aria-label="Gợi ý mô hình nhanh">
                      <span className="chips-hint">Gợi ý nhanh:</span>
                      {[
                        "approved-general@2",
                        "claude-3-5-sonnet",
                        "gpt-4o",
                        "local-llama-3",
                      ].map((model) => (
                        <button
                          key={model}
                          type="button"
                          className={`model-chip ${
                            editorModelRef === model ? "chip-selected" : ""
                          }`}
                          aria-pressed={editorModelRef === model}
                          aria-label={`Chọn mô hình gợi ý ${model}`}
                          onClick={() => {
                            setEditorModelRef(model);
                            invalidateValidation();
                          }}
                        >
                          {model}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="form-field-group">
                    <label htmlFor="edit-mode-select" className="form-field-label">
                      Chế độ thực thi (Execution Mode)
                    </label>
                    <select
                      id="edit-mode-select"
                      value={editorMode}
                      onChange={(e) => {
                        setEditorMode(e.target.value as ExecutionMode);
                        invalidateValidation();
                      }}
                      className="form-select-control"
                      aria-label="Lựa chọn chế độ thực thi"
                    >
                      <option value="fixed_plan">
                        fixed_plan (Thực thi kế hoạch tuần tự)
                      </option>
                      <option value="planner_based">
                        planner_based (Lập kế hoạch đa bước động)
                      </option>
                      <option value="react">
                        react (Vòng lặp Suy luận & Hành động ReAct)
                      </option>
                    </select>
                    <span className="field-hint-caption">
                      {editorMode === "fixed_plan" &&
                        "Kiểm soát luồng chạy nghiêm ngặt theo các bước định sẵn."}
                      {editorMode === "planner_based" &&
                        "Tác tử phân rã mục tiêu và xây dựng đồ thị kế hoạch trước khi hành động."}
                      {editorMode === "react" &&
                        "Tương tác linh hoạt, gọi công cụ động dựa trên phản hồi của từng bước."}
                    </span>
                  </div>
                </div>

                {/* Runtime Bounds with Sliders + Number Inputs */}
                <div className="runtime-bounds-container">
                  <h4 className="bounds-section-title">
                    Ranh giới tài nguyên vận hành (Runtime Bounds)
                  </h4>

                  {/* 1. Max Steps */}
                  <div className="bound-slider-card">
                    <div className="bound-label-row">
                      <label htmlFor="edit-max-steps-input" className="bound-name">
                        Số bước tối đa (Max Steps)
                      </label>
                      <input
                        id="edit-max-steps-input"
                        type="number"
                        min={1}
                        max={50}
                        value={editorMaxSteps}
                        onChange={(e) => {
                          setEditorMaxSteps(Number(e.target.value));
                          invalidateValidation();
                        }}
                        aria-label="Nhập số bước tối đa (từ 1 đến 50)"
                        className="bound-number-input"
                      />
                    </div>
                    <input
                      id="edit-max-steps-slider"
                      type="range"
                      min={1}
                      max={50}
                      value={editorMaxSteps}
                      onChange={(e) => {
                        setEditorMaxSteps(Number(e.target.value));
                        invalidateValidation();
                      }}
                      aria-label="Thanh trượt điều chỉnh Số bước tối đa"
                      aria-valuenow={editorMaxSteps}
                      aria-valuemin={1}
                      aria-valuemax={50}
                      aria-valuetext={`${editorMaxSteps} bước`}
                      aria-controls="edit-max-steps-input"
                      className="bound-slider-input"
                    />
                    <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị số bước">
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxSteps(10)}
                        aria-label="Chọn mốc an toàn 10 bước"
                      >
                        [10] Khuyến nghị Safe
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxSteps(25)}
                        aria-label="Chọn mốc tiêu chuẩn 25 bước"
                      >
                        [25] Chuẩn Standard
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxSteps(50)}
                        aria-label="Chọn mốc tối đa 50 bước"
                      >
                        [50] Tối đa Max
                      </button>
                    </div>
                  </div>

                  {/* 2. Max Wall Time */}
                  <div className="bound-slider-card">
                    <div className="bound-label-row">
                      <label htmlFor="edit-max-walltime-input" className="bound-name">
                        Thời gian chạy tối đa (Max Wall Time - giây)
                      </label>
                      <input
                        id="edit-max-walltime-input"
                        type="number"
                        min={10}
                        max={600}
                        step={10}
                        value={editorMaxWallTime}
                        onChange={(e) => {
                          setEditorMaxWallTime(Number(e.target.value));
                          invalidateValidation();
                        }}
                        aria-label="Nhập thời gian chạy tối đa (từ 10 đến 600 giây)"
                        className="bound-number-input"
                      />
                    </div>
                    <input
                      id="edit-max-walltime-slider"
                      type="range"
                      min={10}
                      max={600}
                      step={10}
                      value={editorMaxWallTime}
                      onChange={(e) => {
                        setEditorMaxWallTime(Number(e.target.value));
                        invalidateValidation();
                      }}
                      aria-label="Thanh trượt điều chỉnh Thời gian chạy tối đa"
                      aria-valuenow={editorMaxWallTime}
                      aria-valuemin={10}
                      aria-valuemax={600}
                      aria-valuetext={`${editorMaxWallTime} giây`}
                      aria-controls="edit-max-walltime-input"
                      className="bound-slider-input"
                    />
                    <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị thời gian chạy">
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxWallTime(60)}
                        aria-label="Chọn mốc tác vụ nhanh 60 giây"
                      >
                        [60s] Tác vụ nhanh
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxWallTime(180)}
                        aria-label="Chọn mốc tiêu chuẩn 180 giây"
                      >
                        [180s] Tiêu chuẩn 3m
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxWallTime(300)}
                        aria-label="Chọn mốc tác vụ dài 300 giây"
                      >
                        [300s] Tác vụ dài 5m
                      </button>
                    </div>
                  </div>

                  {/* 3. Max Tokens */}
                  <div className="bound-slider-card">
                    <div className="bound-label-row">
                      <label htmlFor="edit-max-tokens-input" className="bound-name">
                        Ngân sách Token tối đa (Max Total Tokens)
                      </label>
                      <input
                        id="edit-max-tokens-input"
                        type="number"
                        min={1000}
                        max={128000}
                        step={1000}
                        value={editorMaxTokens}
                        onChange={(e) => {
                          setEditorMaxTokens(Number(e.target.value));
                          invalidateValidation();
                        }}
                        aria-label="Nhập ngân sách Token tối đa (từ 1000 đến 128000)"
                        className="bound-number-input"
                      />
                    </div>
                    <input
                      id="edit-max-tokens-slider"
                      type="range"
                      min={1000}
                      max={128000}
                      step={1000}
                      value={editorMaxTokens}
                      onChange={(e) => {
                        setEditorMaxTokens(Number(e.target.value));
                        invalidateValidation();
                      }}
                      aria-label="Thanh trượt điều chỉnh Ngân sách Token tối đa"
                      aria-valuenow={editorMaxTokens}
                      aria-valuemin={1000}
                      aria-valuemax={128000}
                      aria-valuetext={`${editorMaxTokens} tokens`}
                      aria-controls="edit-max-tokens-input"
                      className="bound-slider-input"
                    />
                    <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị ngân sách Token">
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxTokens(8000)}
                        aria-label="Chọn mốc tiết kiệm 8,000 tokens"
                      >
                        [8k] Tiết kiệm
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxTokens(16000)}
                        aria-label="Chọn mốc chuẩn 16,000 tokens"
                      >
                        [16k] Chuẩn
                      </button>
                      <button
                        type="button"
                        className="tick-mark"
                        onClick={() => setEditorMaxTokens(64000)}
                        aria-label="Chọn mốc ngữ cảnh sâu 64,000 tokens"
                      >
                        [64k] Ngữ cảnh sâu
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 3: DEPENDENCIES & BINDINGS */}
            {activeWorkshopTab === "dependencies" && (
              <div
                id="workshop-tabpanel-dependencies"
                role="tabpanel"
                aria-labelledby="workshop-tab-dependencies"
                tabIndex={0}
                className="tab-pane-dependencies"
              >
                <div className="dependencies-overview-box">
                  <h4 className="dependencies-title">
                    Liên kết Công cụ, Kỹ năng & Cơ sở Tri thức
                  </h4>
                  <p className="dependencies-desc">
                    Tác tử AI liên kết các phụ thuộc ngoại vi thông qua
                    AgentSpec. Tại Bước 04-C, toàn bộ các grant quyền sở hữu và
                    trạng thái parent sẽ được khóa nguyên tử (Atomic Lock) trước
                    khi niêm phong bất biến.
                  </p>
                </div>

                <div className="dependencies-category-list">
                  {/* Tools */}
                  <div className="dep-group">
                    <h5 className="dep-group-title">
                      🛠️ Tools ({activeDependencies.tools.length})
                    </h5>
                    {activeDependencies.tools.length === 0 ? (
                      <div className="dep-empty-card">
                        Chưa liên kết công cụ nào. Tác tử đang chạy ở chế độ
                        thuần mô hình (Prompt Only).
                      </div>
                    ) : (
                      <div className="dep-cards-grid" role="list" aria-label="Danh sách công cụ Tools liên kết">
                        {activeDependencies.tools.map((t) => (
                          <div key={String(t)} className="dep-item-card" role="listitem">
                            <span className="dep-item-name">{String(t)}</span>
                            <span className="dep-status-badge badge-granted">
                              ✓ Granted
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Skills */}
                  <div className="dep-group">
                    <h5 className="dep-group-title">
                      ⚡ Skills ({activeDependencies.skills.length})
                    </h5>
                    {activeDependencies.skills.length === 0 ? (
                      <div className="dep-empty-card">
                        Chưa liên kết kỹ năng nào.
                      </div>
                    ) : (
                      <div className="dep-cards-grid" role="list" aria-label="Danh sách kỹ năng Skills liên kết">
                        {activeDependencies.skills.map((s) => (
                          <div key={String(s)} className="dep-item-card" role="listitem">
                            <span className="dep-item-name">{String(s)}</span>
                            <span className="dep-status-badge badge-granted">
                              ✓ Granted
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Knowledge Bases */}
                  <div className="dep-group">
                    <h5 className="dep-group-title">
                      📚 Knowledge Bases ({activeDependencies.knowledge.length})
                    </h5>
                    {activeDependencies.knowledge.length === 0 ? (
                      <div className="dep-empty-card">
                        Chưa liên kết cơ sở tri thức nào.
                      </div>
                    ) : (
                      <div className="dep-cards-grid" role="list" aria-label="Danh sách cơ sở tri thức Knowledge Bases liên kết">
                        {activeDependencies.knowledge.map((k) => (
                          <div key={String(k)} className="dep-item-card" role="listitem">
                            <span className="dep-item-name">{String(k)}</span>
                            <span className="dep-status-badge badge-granted">
                              ✓ Granted
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </form>
        </div>
      </Modal>

      {/* ========================================================
          MODAL: HIGH-TRUST PUBLISH WORKFLOW (Pre-flight / Stepper / Recovery / Certificate)
          ======================================================== */}
      <Modal
        isOpen={Boolean(publishTargetAgent)}
        onClose={handleClosePublishModal}
        title={
          publishReceipt
            ? "Chứng nhận Xuất bản Bất biến (Immutable Release Certificate)"
            : isPublishing
            ? "Đang phát hành Phiên bản Bất biến..."
            : isRecoveredIntent
            ? "Khôi phục Ý định Xuất bản (Recover Pending Intent)"
            : "Xuất bản Phiên bản Bất biến (Publish Release)"
        }
        description={
          publishReceipt
            ? `Tác tử: ${publishTargetAgent?.display_name} | Phiên bản chính thức v${publishReceipt.version}`
            : isPublishing
            ? "Đang thực hiện quy trình niêm phong giao dịch đa bước RFC 8785"
            : `Tác tử: ${publishTargetAgent?.display_name} (${publishTargetAgent?.slug})`
        }
        maxWidth="620px"
        footer={
          publishReceipt ? (
            <>
              <Button
                variant="secondary"
                size="md"
                type="button"
                onClick={handleClosePublishModal}
              >
                Đóng & Tiếp tục
              </Button>
              <Button
                variant="primary"
                size="md"
                type="button"
                onClick={() => {
                  handleClosePublishModal();
                  router.push(`/w/${workspaceId}/overview`);
                }}
              >
                Xem Tổng quan Không gian →
              </Button>
            </>
          ) : isPublishing ? (
            <Button variant="secondary" size="md" type="button" disabled>
              Đang niêm phong giao dịch...
            </Button>
          ) : (
            <>
              <Button
                variant="secondary"
                size="md"
                type="button"
                onClick={handleClosePublishModal}
                disabled={isPublishing}
              >
                Hủy
              </Button>
              {isRecoveredIntent && (
                <Button
                  variant="outline"
                  size="md"
                  type="button"
                  onClick={handleCreateFreshIntent}
                  disabled={isPublishing}
                  title="Hủy bỏ khôi phục và tạo khóa xuất bản mới cho bản nháp hiện tại"
                  aria-label="Hủy bỏ khôi phục và tạo khóa xuất bản mới cho bản nháp hiện tại"
                >
                  Hủy bỏ & Tạo bản mới
                </Button>
              )}
              <Button
                variant="copper"
                size="md"
                type="button"
                onClick={handleConfirmPublish}
                isLoading={isPublishing}
                aria-label={
                  isRecoveredIntent
                    ? "Tiếp tục kiểm tra và khôi phục giao dịch xuất bản"
                    : "Xác nhận xuất bản phiên bản bất biến RFC 8785"
                }
              >
                {isRecoveredIntent
                  ? "Tiếp tục kiểm tra & Khôi phục (Verify & Recover)"
                  : "Xác nhận Xuất bản (RFC 8785)"}
              </Button>
            </>
          )
        }
      >
        <div className="publish-modal-body">
          {/* STATE 1: DIGITAL RELEASE CERTIFICATE (Post-Success) */}
          {publishReceipt && (
            <div className="digital-release-certificate" role="region" aria-label="Chứng nhận phát hành bất biến">
              <div className="certificate-emblem-header">
                <div className="certificate-seal-badge" aria-hidden="true">
                  <svg
                    width="24"
                    height="24"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--color-success)"
                    strokeWidth="2.5"
                  >
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                    <polyline points="9 12 11 14 15 10" />
                  </svg>
                </div>
                <div className="certificate-titles">
                  <span className="certificate-micro-tag">
                    CHỨNG NHẬN NIÊM PHONG BẤT BIẾN (IMMUTABLE SEAL)
                  </span>
                  <h3 className="certificate-main-title">
                    Phiên bản phát hành v{publishReceipt.version}
                  </h3>
                  <span className="certificate-sub">
                    Giao dịch đã được ghi nhận bền vững vào sổ cái
                    runtime.tool_operations
                  </span>
                </div>
              </div>

              <div className="certificate-details-card">
                <div className="cert-row">
                  <span className="cert-label">Tác tử AI:</span>
                  <strong className="cert-val">
                    {publishTargetAgent?.display_name} ({publishTargetAgent?.slug})
                  </strong>
                </div>

                <div className="cert-row">
                  <span className="cert-label">Version ID:</span>
                  <span className="cert-val cert-mono">
                    {publishReceipt.agent_version_id}
                  </span>
                </div>

                <div className="cert-row-digest">
                  <div className="cert-digest-header">
                    <span className="cert-label">Bundle SHA-256 Digest:</span>
                    <button
                      type="button"
                      className="cert-copy-btn"
                      onClick={() =>
                        handleCopyText(publishReceipt.bundle_sha256, "SHA-256")
                      }
                      title="Sao chép mã SHA-256 vào clipboard"
                      aria-label="Sao chép mã SHA-256 vào clipboard"
                    >
                      {copiedKey === "SHA-256" ? "✓ Đã sao chép" : "Sao chép SHA-256"}
                    </button>
                  </div>
                  <div className="cert-hash-box cert-mono">
                    {publishReceipt.bundle_sha256}
                  </div>
                </div>

                <div className="cert-row">
                  <span className="cert-label">Thời điểm niêm phong:</span>
                  <span className="cert-val">
                    {formatDate(publishReceipt.created_at)}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* STATE 2: VISUAL IN-FLIGHT STEPPER (During Publishing) */}
          {isPublishing && !publishReceipt && (
            <div className="publish-stepper-container" role="region" aria-label="Tiến trình niêm phong phiên bản">
              <div className="stepper-lead-text">
                Đang xử lý xuất bản phiên bản an toàn cấp độ FinTech...
              </div>

              <ol className="stepper-track" role="list" aria-label="Các bước niêm phong giao dịch xuất bản">
                {/* Step 1 */}
                <li
                  className={`stepper-node ${
                    publishStep > 1
                      ? "step-done"
                      : publishStep === 1
                      ? "step-active"
                      : "step-waiting"
                  }`}
                  role="listitem"
                  aria-current={publishStep === 1 ? "step" : undefined}
                >
                  <div className="node-icon-circle" aria-hidden="true">
                    {publishStep > 1 ? "✓" : "1"}
                  </div>
                  <div className="node-content">
                    <div className="node-title">
                      1. Khóa trạng thái tài nguyên (Resource Locking)
                    </div>
                    <div className="node-desc">
                      Khóa FOR SHARE các Tool grants, Skill parents & Knowledge bindings
                    </div>
                  </div>
                </li>

                {/* Step 2 */}
                <li
                  className={`stepper-node ${
                    publishStep > 2
                      ? "step-done"
                      : publishStep === 2
                      ? "step-active"
                      : "step-waiting"
                  }`}
                  role="listitem"
                  aria-current={publishStep === 2 ? "step" : undefined}
                >
                  <div className="node-icon-circle" aria-hidden="true">
                    {publishStep > 2 ? "✓" : "2"}
                  </div>
                  <div className="node-content">
                    <div className="node-title">
                      2. Đóng dấu niêm phong phiên bản (Sealing Version)
                    </div>
                    <div className="node-desc">
                      Chuẩn hóa JSON RFC 8785 và sinh bundle SHA-256 digest
                    </div>
                  </div>
                </li>

                {/* Step 3 */}
                <li
                  className={`stepper-node ${
                    publishReceipt
                      ? "step-done"
                      : publishStep === 3
                      ? "step-active"
                      : "step-waiting"
                  }`}
                  role="listitem"
                  aria-current={publishStep === 3 ? "step" : undefined}
                >
                  <div className="node-icon-circle" aria-hidden="true">
                    {publishReceipt ? "✓" : "3"}
                  </div>
                  <div className="node-content">
                    <div className="node-title">
                      3. Lưu biên nhận bền vững (Committing Receipt Ledger)
                    </div>
                    <div className="node-desc">
                      Ghi nhận giao dịch runtime.tool_operations và hoàn tất
                    </div>
                  </div>
                </li>
              </ol>
            </div>
          )}

          {/* STATE 3: INTENT RECOVERY BANNER (When previous intent unresolved) */}
          {!isPublishing && !publishReceipt && isRecoveredIntent && (
            <div className="intent-recovery-card" role="alert" aria-live="polite">
              <div className="recovery-header-row">
                <span className="recovery-badge">
                  ⚠️ PHÁT HIỆN YÊU CẦU CHƯA XÁC NHẬN (PENDING INTENT)
                </span>
                <span className="recovery-timestamp">
                  Khởi tạo: {formatRelativeTime(pendingCreatedAt)}
                </span>
              </div>

              <h4 className="recovery-title">
                Phát hiện yêu cầu xuất bản chưa nhận được xác nhận từ máy chủ
              </h4>
              <p className="recovery-explanation">
                Yêu cầu xuất bản trước đó có thể đã được lưu trữ an toàn trên cơ sở
                dữ liệu nhưng kết nối mạng bị gián đoạn trước khi nhận được biên
                nhận. Chọn <strong>Verify & Recover</strong> để truy vấn lại
                ngay với đúng khóa giao dịch cũ mà không tạo thêm phiên bản thừa.
              </p>

              <div className="recovery-meta-strip">
                <div className="meta-kv">
                  <span className="k">Khóa Idempotency gốc:</span>
                  <span className="v cert-mono">{publishIdempotencyKey}</span>
                  <button
                    type="button"
                    className="copy-mini-btn"
                    onClick={() =>
                      handleCopyText(publishIdempotencyKey, "Khóa Idempotency")
                    }
                    title="Sao chép Khóa Idempotency vào clipboard"
                    aria-label="Sao chép Khóa Idempotency vào clipboard"
                  >
                    {copiedKey === "Khóa Idempotency" ? "✓" : "Sao chép"}
                  </button>
                </div>
                <div className="meta-kv">
                  <span className="k">Revision dự kiến:</span>
                  <span className="v">rev {publishRevision}</span>
                  {publishRevision !== publishTargetAgent?.revision && (
                    <span className="rev-diff-tag">
                      (Bản nháp hiện tại đã lên rev {publishTargetAgent?.revision})
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* STATE 4: PRE-FLIGHT RELEASE CHECKLIST (Standard Fresh Publish) */}
          {!isPublishing && !publishReceipt && !isRecoveredIntent && (
            <div className="preflight-release-section">
              <div className="preflight-lead-desc">
                Quy trình xuất bản tác tử tuân thủ nghiêm ngặt chuẩn kiến trúc
                FinTech-grade: bảo đảm tính bất biến tuyệt đối của AgentSpec,
                chống thay đổi lén lút (Anti-tamper) và khóa tương tranh nguyên tử.
              </div>

              <ul className="preflight-checklist-grid" role="list" aria-label="Danh sách kiểm tra trước khi phát hành">
                <li className="checklist-item-card" role="listitem">
                  <div className="check-icon-circle" aria-hidden="true">✓</div>
                  <div className="check-text-group">
                    <span className="check-title">
                      Manifest Canonicalization (RFC 8785)
                    </span>
                    <span className="check-desc">
                      Chuẩn hóa JSON Canonicalization Scheme (JCS) độc lập thứ
                      tự key, tạo digest đồng nhất.
                    </span>
                  </div>
                </li>

                <li className="checklist-item-card" role="listitem">
                  <div className="check-icon-circle" aria-hidden="true">✓</div>
                  <div className="check-text-group">
                    <span className="check-title">
                      Immutable Binding Sealing
                    </span>
                    <span className="check-desc">
                      Khóa FOR SHARE các liên kết Tools, Skills, và Knowledge
                      Bases trong cùng một transaction.
                    </span>
                  </div>
                </li>

                <li className="checklist-item-card" role="listitem">
                  <div className="check-icon-circle" aria-hidden="true">✓</div>
                  <div className="check-text-group">
                    <span className="check-title">
                      Model Settings Snapshot Freeze
                    </span>
                    <span className="check-desc">
                      Đóng băng thông số sinh (temperature, top_p, model_config_id)
                      vào mã băm chứng thực.
                    </span>
                  </div>
                </li>

                <li className="checklist-item-card" role="listitem">
                  <div className="check-icon-circle" aria-hidden="true">✓</div>
                  <div className="check-text-group">
                    <span className="check-title">
                      Idempotency & Replay Protection
                    </span>
                    <span className="check-desc">
                      Khóa giao dịch duy nhất ràng buộc theo
                      (tenant_id, workspace_id, operation_key).
                    </span>
                  </div>
                </li>
              </ul>

              <div className="preflight-key-box">
                <div className="key-box-row">
                  <span className="key-box-label">Target Revision:</span>
                  <span className="agent-rev-token">rev {publishRevision}</span>
                </div>
                <div className="key-box-row">
                  <span className="key-box-label">Idempotency Key:</span>
                  <span className="key-box-mono cert-mono">
                    {publishIdempotencyKey}
                  </span>
                  <button
                    type="button"
                    className="copy-mini-btn"
                    onClick={() =>
                      handleCopyText(publishIdempotencyKey, "Khóa Idempotency")
                    }
                    title="Sao chép Khóa Idempotency vào clipboard"
                    aria-label="Sao chép Khóa Idempotency vào clipboard"
                  >
                    {copiedKey === "Khóa Idempotency" ? "✓" : "Sao chép"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Feedback Error / Alert Banner */}
          {publishMessage && (
            <div className="publish-feedback-banner" role="alert" aria-live="assertive">
              <span aria-hidden="true">✕</span> {publishMessage}
            </div>
          )}
        </div>
      </Modal>

      {/* Scoped CSS Modules & Tokens */}
      <style>{`
        .agents-page-root {
          width: 100%;
        }

        /* WCAG 2.2 AA Focus Visibility */
        :focus-visible {
          outline: 2px solid var(--color-primary, #6366f1) !important;
          outline-offset: 2px !important;
        }

        /* Screen Reader Accessible Live & Label utility */
        .visually-hidden {
          position: absolute !important;
          width: 1px !important;
          height: 1px !important;
          padding: 0 !important;
          margin: -1px !important;
          overflow: hidden !important;
          clip: rect(0, 0, 0, 0) !important;
          white-space: nowrap !important;
          border: 0 !important;
        }

        /* Keyboard shortcut badge */
        .search-shortcut-badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 22px;
          height: 22px;
          padding: 0 4px;
          font-size: 11px;
          font-family: var(--font-mono, monospace);
          font-weight: 600;
          color: var(--text-muted);
          background-color: var(--bg-surface-elevated, var(--bg-surface));
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-xs);
          pointer-events: none;
        }

        /* Fixed Toast Notification */
        .agent-toast-card {
          position: fixed;
          bottom: 24px;
          right: 24px;
          background-color: var(--bg-surface-elevated);
          color: var(--text-primary);
          border-radius: var(--radius-md);
          border: 1px solid var(--border-strong);
          box-shadow: var(--shadow-lg);
          font-size: 13px;
          z-index: 1000;
          overflow: hidden;
          min-width: 320px;
          max-width: 440px;
          animation: toast-slide-in 200ms cubic-bezier(0.16, 1, 0.3, 1);
        }

        @keyframes toast-slide-in {
          from {
            opacity: 0;
            transform: translateY(12px) scale(0.97);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }

        .toast-content-wrapper {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px 16px;
        }

        .toast-icon {
          flex-shrink: 0;
        }

        .icon-success {
          color: var(--color-success);
        }

        .icon-error {
          color: var(--color-danger);
        }

        .icon-info {
          color: var(--color-info);
        }

        .toast-text {
          flex: 1;
          font-size: 13px;
          line-height: 1.4;
          color: var(--text-primary);
        }

        .toast-dismiss-button {
          flex-shrink: 0;
          background: none;
          border: none;
          color: var(--text-muted);
          cursor: pointer;
          font-size: 14px;
          padding: 2px 6px;
          border-radius: var(--radius-xs);
          transition: color var(--transition-fast);
        }

        .toast-dismiss-button:hover {
          color: var(--text-primary);
        }

        .toast-progress-bar {
          height: 2.5px;
          background: var(--color-primary);
          animation: toast-progress 4s linear forwards;
        }

        .toast-error .toast-progress-bar {
          background: var(--color-danger);
        }

        .toast-info .toast-progress-bar {
          background: var(--color-info);
        }

        @keyframes toast-progress {
          from {
            width: 100%;
          }
          to {
            width: 0%;
          }
        }

        /* Identicon */
        .agent-identicon {
          border-radius: var(--radius-md);
          border: 1px solid;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          position: relative;
          user-select: none;
          flex-shrink: 0;
          font-family: var(--font-mono);
          font-weight: 700;
        }

        .identicon-sm {
          width: 26px;
          height: 26px;
          font-size: 10px;
        }

        .identicon-md {
          width: 34px;
          height: 34px;
          font-size: 12px;
        }

        .identicon-lg {
          width: 42px;
          height: 42px;
          font-size: 14px;
        }

        .identicon-letters {
          line-height: 1;
        }

        .identicon-indicator {
          position: absolute;
          bottom: -1px;
          right: -1px;
          width: 7px;
          height: 7px;
          border-radius: var(--radius-pill);
          border: 1.5px solid var(--bg-surface);
        }

        /* Status Pills & Dots */
        .agent-status-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 2px 8px;
          border-radius: var(--radius-pill);
          font-size: 11px;
          font-weight: 500;
        }

        .status-draft {
          background-color: var(--color-warning-bg);
          color: var(--color-warning);
          border: 1px solid var(--color-warning-border);
        }

        .status-archived {
          background-color: var(--bg-subtle);
          color: var(--text-muted);
          border: 1px solid var(--border-subtle);
        }

        .status-dot {
          width: 6px;
          height: 6px;
          border-radius: var(--radius-pill);
        }

        .dot-draft-pulse {
          background-color: var(--color-warning);
          animation: pulse-dot 2s infinite ease-in-out;
        }

        @keyframes pulse-dot {
          0%, 100% {
            opacity: 1;
            transform: scale(1);
          }
          50% {
            opacity: 0.35;
            transform: scale(0.85);
          }
        }

        .dot-archived {
          background-color: var(--text-muted);
        }

        /* Table Identity Cell */
        .agent-identity-cell {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .agent-identity-info {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }

        .agent-title-text {
          font-weight: 600;
          color: var(--text-primary);
          font-size: 13px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .agent-slug-badge {
          font-family: var(--font-mono);
          font-size: 11px;
          color: var(--text-muted);
          background-color: var(--bg-subtle);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
          display: inline-block;
          width: fit-content;
        }

        .agent-rev-token {
          font-family: var(--font-mono);
          font-size: 12px;
          font-weight: 600;
          color: var(--color-primary);
          background-color: var(--color-primary-subtle);
          padding: 2px 7px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--color-primary-border);
        }

        .agent-date-text {
          font-size: 12px;
          color: var(--text-muted);
        }

        .agent-actions-row {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 6px;
        }

        /* Console Toolbar */
        .agents-console-toolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 16px;
        }

        .toolbar-search-filter-group {
          display: flex;
          align-items: center;
          gap: 8px;
          flex: 1;
          min-width: 280px;
          max-width: 620px;
        }

        .toolbar-search-input {
          position: relative;
          display: flex;
          align-items: center;
          flex: 1;
        }

        .search-leading-icon {
          position: absolute;
          left: 10px;
          color: var(--text-muted);
          pointer-events: none;
        }

        .search-field {
          width: 100%;
          height: 34px;
          padding: 0 28px 0 32px;
          font-size: 13px;
          background-color: var(--bg-surface);
          color: var(--text-primary);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          outline: none;
          transition: border-color var(--transition-fast);
        }

        .search-field:focus {
          border-color: var(--border-focus);
        }

        .search-clear-btn {
          position: absolute;
          right: 8px;
          background: none;
          border: none;
          color: var(--text-muted);
          cursor: pointer;
          font-size: 12px;
          padding: 2px;
        }

        .toolbar-select {
          height: 34px;
          padding: 0 10px;
          font-size: 12px;
          background-color: var(--bg-surface);
          color: var(--text-primary);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          cursor: pointer;
          outline: none;
        }

        .toolbar-view-switcher-group {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .filter-reset-link {
          background: none;
          border: none;
          font-size: 12px;
          color: var(--color-primary);
          cursor: pointer;
          text-decoration: underline;
        }

        .view-mode-segmented-control {
          display: flex;
          align-items: center;
          background-color: var(--bg-subtle);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          padding: 2px;
          gap: 2px;
        }

        .view-mode-button {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          padding: 4px 10px;
          font-size: 12px;
          font-weight: 500;
          color: var(--text-secondary);
          border-radius: var(--radius-xs);
          border: none;
          background: none;
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .view-mode-button.active {
          background-color: var(--bg-surface);
          color: var(--text-primary);
          box-shadow: var(--shadow-sm);
        }

        /* Desktop Table Card */
        .agent-desktop-table-card {
          width: 100%;
        }

        /* Grid Cards View */
        .agent-grid-cards-container {
          width: 100%;
        }

        .agents-cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 16px;
          margin-bottom: 20px;
        }

        .agent-b2b-card {
          background-color: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-lg);
          padding: 16px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          transition: transform var(--transition-fast), box-shadow var(--transition-fast), border-color var(--transition-fast);
        }

        .agent-b2b-card:hover {
          transform: translateY(-2px);
          border-color: var(--color-primary-border);
          box-shadow: var(--shadow-md);
        }

        .card-top-identity {
          display: flex;
          align-items: flex-start;
          gap: 12px;
          margin-bottom: 12px;
        }

        .card-title-group {
          min-width: 0;
          flex: 1;
        }

        .card-agent-name {
          font-size: 14px;
          font-weight: 600;
          color: var(--text-primary);
          margin-bottom: 4px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .card-agent-slug {
          font-family: var(--font-mono);
          font-size: 11px;
          color: var(--text-muted);
          background-color: var(--bg-subtle);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
          display: inline-block;
        }

        .card-meta-section {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-bottom: 14px;
          flex: 1;
        }

        .card-status-badges {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .card-rev-badge {
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 600;
          color: var(--color-primary);
          background-color: var(--color-primary-subtle);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--color-primary-border);
        }

        .card-date-info {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          color: var(--text-muted);
        }

        .card-action-footer {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          padding-top: 12px;
          border-top: 1px solid var(--border-subtle);
        }

        /* Starter Templates Section (Empty State) */
        .agents-empty-wrapper {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 24px;
        }

        .starter-templates-section {
          width: 100%;
          max-width: 960px;
        }

        .starter-templates-heading {
          display: flex;
          align-items: center;
          gap: 16px;
          margin-bottom: 16px;
        }

        .templates-heading-line {
          flex: 1;
          height: 1px;
          background-color: var(--border-subtle);
        }

        .templates-heading-text {
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.05em;
          color: var(--text-muted);
          white-space: nowrap;
        }

        .starter-templates-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 16px;
        }

        .starter-template-card {
          background-color: var(--bg-surface);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-md);
          padding: 16px;
          cursor: pointer;
          transition: all var(--transition-fast);
          display: flex;
          flex-direction: column;
          justify-content: space-between;
        }

        .starter-template-card:hover {
          border-color: var(--color-primary);
          transform: translateY(-2px);
          box-shadow: var(--shadow-sm);
        }

        .template-card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 10px;
        }

        .template-mode-pill {
          font-size: 10px;
          font-weight: 600;
          color: var(--color-copper);
          background-color: var(--color-copper-subtle);
          padding: 2px 6px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--color-copper-border);
          font-family: var(--font-mono);
        }

        .template-card-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
          margin-bottom: 6px;
        }

        .template-card-desc {
          font-size: 12px;
          color: var(--text-secondary);
          line-height: 1.45;
          margin-bottom: 14px;
          flex: 1;
        }

        .template-card-footer {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          padding-top: 8px;
          border-top: 1px solid var(--border-subtle);
        }

        .template-apply-action {
          font-size: 12px;
          font-weight: 500;
          color: var(--color-primary);
        }

        /* ========================================================
           AGENT STUDIO WORKSHOP STYLING
           ======================================================== */
        .workshop-container {
          display: flex;
          flex-direction: column;
          gap: 16px;
          width: 100%;
        }

        /* Diagnostic Center Box */
        .diagnostic-center-box {
          border-radius: var(--radius-sm);
          padding: 12px 14px;
          font-size: 12px;
          display: flex;
          flex-direction: column;
          gap: 10px;
          transition: all var(--transition-fast);
        }

        .diagnostic-ready {
          background-color: var(--color-success-bg, #f0fdf4);
          border: 1px solid var(--color-success-border, #bbf7d0);
        }

        .diagnostic-issues {
          background-color: var(--color-warning-bg);
          border: 1px solid var(--color-warning-border);
        }

        .diagnostic-header-bar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          flex-wrap: wrap;
        }

        .diagnostic-status-indicator {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .diagnostic-badge-success {
          font-weight: 700;
          color: var(--color-success);
          letter-spacing: 0.02em;
        }

        .diagnostic-badge-danger {
          font-weight: 700;
          color: var(--color-danger);
          letter-spacing: 0.02em;
        }

        .diagnostic-rev-tag {
          font-family: var(--font-mono);
          font-size: 11px;
          color: var(--text-muted);
        }

        .diagnostic-stale-pill {
          font-size: 11px;
          font-weight: 600;
          color: var(--color-warning);
          background-color: rgba(217, 119, 6, 0.12);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
        }

        .diagnostic-recheck-btn {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 11px;
          font-weight: 600;
          padding: 3px 8px;
          background-color: var(--bg-surface);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-xs);
          color: var(--text-primary);
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .diagnostic-recheck-btn:hover {
          border-color: var(--color-primary);
          color: var(--color-primary);
        }

        .diagnostic-issues-grid {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin-top: 4px;
        }

        .diagnostic-group-section {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .diagnostic-group-header {
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.02em;
        }

        .diagnostic-items-list {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .diagnostic-issue-card {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 6px 10px;
          background-color: var(--bg-surface);
          border-radius: var(--radius-xs);
          border-left: 3px solid;
          font-size: 12px;
        }

        .card-critical {
          border-left-color: var(--color-danger);
        }

        .card-warning {
          border-left-color: var(--color-warning);
        }

        .issue-content {
          flex: 1;
          color: var(--text-primary);
          line-height: 1.4;
        }

        .issue-field {
          font-family: var(--font-mono);
          font-weight: 600;
        }

        .issue-code {
          color: var(--text-muted);
          font-family: var(--font-mono);
          font-size: 11px;
        }

        .issue-focus-btn {
          background: none;
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-xs);
          padding: 2px 7px;
          font-size: 11px;
          font-weight: 500;
          color: var(--color-primary);
          cursor: pointer;
          white-space: nowrap;
          transition: all var(--transition-fast);
        }

        .issue-focus-btn:hover {
          background-color: var(--color-primary-subtle);
          border-color: var(--color-primary-border);
        }

        /* 412 Conflict Side-by-Side Diff Workbench */
        .conflict-workbench-card {
          background-color: var(--bg-subtle);
          border: 1px solid var(--color-warning-border);
          border-radius: var(--radius-sm);
          padding: 14px;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .conflict-workbench-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        }

        .conflict-icon-title {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          flex: 1;
        }

        .conflict-title-text {
          font-size: 13px;
          font-weight: 700;
          color: var(--color-warning);
          margin-bottom: 2px;
        }

        .conflict-desc-text {
          font-size: 12px;
          color: var(--text-secondary);
          margin: 0;
        }

        .conflict-decision-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .conflict-diff-table {
          width: 100%;
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-xs);
          background-color: var(--bg-surface);
          overflow: hidden;
          font-size: 12px;
        }

        .diff-table-header {
          display: grid;
          grid-template-columns: 28% 36% 36%;
          background-color: var(--bg-subtle);
          border-bottom: 1px solid var(--border-subtle);
          font-weight: 600;
          color: var(--text-muted);
          padding: 6px 10px;
          font-size: 11px;
        }

        .diff-table-row {
          display: grid;
          grid-template-columns: 28% 36% 36%;
          padding: 8px 10px;
          border-bottom: 1px solid var(--border-subtle);
          align-items: center;
        }

        .diff-table-row:last-child {
          border-bottom: none;
        }

        .row-highlight-diff {
          background-color: rgba(217, 119, 6, 0.06);
          border-left: 3px solid var(--color-warning);
        }

        .diff-cell-field {
          font-weight: 500;
          color: var(--text-primary);
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .diff-change-badge {
          font-size: 9px;
          font-weight: 700;
          color: var(--color-warning);
          background-color: var(--color-warning-bg);
          padding: 1px 4px;
          border-radius: 2px;
          text-transform: uppercase;
        }

        .diff-cell-server {
          font-family: var(--font-mono);
          color: var(--text-secondary);
          word-break: break-word;
          font-size: 11px;
        }

        .diff-cell-local {
          font-family: var(--font-mono);
          color: var(--color-primary);
          font-weight: 600;
          word-break: break-word;
          font-size: 11px;
        }

        /* Workshop Navigation Tabs */
        .workshop-nav-tabs {
          display: flex;
          align-items: center;
          gap: 4px;
          border-bottom: 1px solid var(--border-subtle);
          padding-bottom: 6px;
        }

        .workshop-tab-btn {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          font-size: 12px;
          font-weight: 500;
          color: var(--text-secondary);
          border-radius: var(--radius-xs);
          border: none;
          background: none;
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .workshop-tab-btn:hover {
          color: var(--text-primary);
          background-color: var(--bg-subtle);
        }

        .workshop-tab-btn.tab-active {
          color: var(--color-primary);
          background-color: var(--color-primary-subtle);
          font-weight: 600;
        }

        /* Tab 1: Persona Editor with Line Numbers */
        .tab-pane-persona {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .persona-meta-strip {
          display: flex;
          align-items: center;
          gap: 16px;
          flex-wrap: wrap;
          padding: 6px 10px;
          background-color: var(--bg-subtle);
          border-radius: var(--radius-xs);
          font-size: 11px;
        }

        .meta-info-item {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .meta-label {
          color: var(--text-muted);
        }

        .meta-mono-val {
          font-family: var(--font-mono);
          font-weight: 600;
          color: var(--text-primary);
        }

        .meta-token-count {
          font-family: var(--font-mono);
          color: var(--text-primary);
          font-weight: 500;
        }

        .safety-badge-safe {
          font-size: 11px;
          font-weight: 600;
          color: var(--color-success);
          background-color: var(--color-success-bg);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
        }

        .safety-badge-warning {
          font-size: 11px;
          font-weight: 600;
          color: var(--color-warning);
          background-color: var(--color-warning-bg);
          padding: 1px 6px;
          border-radius: var(--radius-xs);
          animation: pulse-dot 2s infinite ease-in-out;
        }

        .persona-editor-wrapper {
          display: flex;
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          background-color: var(--bg-surface);
          overflow: hidden;
          transition: border-color var(--transition-fast);
        }

        .persona-editor-wrapper:focus-within {
          border-color: var(--border-focus);
        }

        .editor-line-gutter {
          width: 40px;
          padding: 8px 6px;
          background-color: var(--bg-subtle);
          border-right: 1px solid var(--border-subtle);
          user-select: none;
          overflow-y: hidden;
          font-family: var(--font-mono);
          font-size: 12px;
          line-height: 20px;
          text-align: right;
          color: var(--text-muted);
        }

        .line-num {
          height: 20px;
        }

        .editor-textarea-mono {
          flex: 1;
          padding: 8px 12px;
          font-family: var(--font-mono);
          font-size: 12px;
          line-height: 20px;
          color: var(--text-primary);
          background: transparent;
          border: none;
          outline: none;
          resize: vertical;
          white-space: pre;
          overflow-wrap: normal;
        }

        .persona-editor-footer {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 12px;
        }

        .editor-hint-text {
          color: var(--text-muted);
          line-height: 1.4;
        }

        /* Tab 2: Model & Runtime Bounds */
        .tab-pane-model {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .model-selection-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 16px;
        }

        .quick-model-chips {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
          margin-top: 6px;
        }

        .chips-hint {
          font-size: 11px;
          color: var(--text-muted);
        }

        .model-chip {
          background-color: var(--bg-subtle);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-xs);
          padding: 2px 6px;
          font-size: 11px;
          font-family: var(--font-mono);
          color: var(--text-secondary);
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .model-chip:hover {
          border-color: var(--color-primary);
          color: var(--color-primary);
        }

        .chip-selected {
          background-color: var(--color-primary-subtle);
          border-color: var(--color-primary-border);
          color: var(--color-primary);
          font-weight: 600;
        }

        .field-hint-caption {
          font-size: 11px;
          color: var(--text-muted);
          margin-top: 4px;
        }

        .runtime-bounds-container {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 14px;
          background-color: var(--bg-subtle);
          border-radius: var(--radius-sm);
          border: 1px solid var(--border-subtle);
        }

        .bounds-section-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
          margin: 0;
        }

        .bound-slider-card {
          display: flex;
          flex-direction: column;
          gap: 6px;
          background-color: var(--bg-surface);
          padding: 10px 12px;
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
        }

        .bound-label-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        .bound-name {
          font-size: 12px;
          font-weight: 500;
          color: var(--text-primary);
        }

        .bound-number-input {
          width: 80px;
          height: 28px;
          padding: 0 8px;
          font-size: 12px;
          font-family: var(--font-mono);
          font-weight: 600;
          color: var(--color-primary);
          background-color: var(--bg-subtle);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-xs);
          text-align: right;
          outline: none;
        }

        .bound-slider-input {
          width: 100%;
          cursor: pointer;
          accent-color: var(--color-primary);
        }

        .slider-ticks-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 11px;
          color: var(--text-muted);
        }

        .tick-mark,
        button.tick-mark {
          background: transparent;
          border: none;
          font-size: 11px;
          color: var(--text-muted);
          cursor: pointer;
          padding: 2px 4px;
          border-radius: var(--radius-xs);
          transition: color var(--transition-fast), background-color var(--transition-fast);
        }

        .tick-mark:hover,
        button.tick-mark:hover {
          color: var(--color-primary);
          background-color: var(--bg-surface-elevated, rgba(255, 255, 255, 0.08));
        }

        button.tick-mark:focus-visible {
          outline: 2px solid var(--color-primary) !important;
          outline-offset: 1px !important;
        }

        /* Tab 3: Dependencies & Bindings */
        .tab-pane-dependencies {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .dependencies-overview-box {
          padding: 12px;
          background-color: var(--bg-subtle);
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
        }

        .dependencies-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
          margin-bottom: 4px;
        }

        .dependencies-desc {
          font-size: 12px;
          color: var(--text-secondary);
          line-height: 1.45;
          margin: 0;
        }

        .dependencies-category-list {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .dep-group {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .dep-group-title {
          font-size: 12px;
          font-weight: 600;
          color: var(--text-primary);
          margin: 0;
        }

        .dep-empty-card {
          padding: 10px 14px;
          background-color: var(--bg-surface);
          border: 1px dashed var(--border-strong);
          border-radius: var(--radius-xs);
          color: var(--text-muted);
          font-size: 12px;
        }

        .dep-cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
          gap: 8px;
        }

        .dep-item-card {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          padding: 8px 12px;
          background-color: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-xs);
        }

        .dep-item-name {
          font-family: var(--font-mono);
          font-size: 12px;
          font-weight: 500;
          color: var(--text-primary);
        }

        .dep-status-badge {
          font-size: 10px;
          font-weight: 600;
          padding: 1px 6px;
          border-radius: var(--radius-xs);
        }

        .badge-granted {
          color: var(--color-success);
          background-color: var(--color-success-bg);
          border: 1px solid var(--color-success-border);
        }

        /* Focus Animation Pulse */
        .field-focus-pulse {
          animation: input-pulse-highlight 1.5s ease-in-out;
        }

        @keyframes input-pulse-highlight {
          0%, 100% {
            outline: 2px solid transparent;
            box-shadow: none;
          }
          30%, 70% {
            outline: 2px solid var(--color-primary);
            box-shadow: 0 0 0 4px var(--color-primary-subtle);
          }
        }

        .icon-spin {
          animation: spin 1s linear infinite;
        }

        @keyframes spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }

        /* Form Controls */
        .modal-form-vertical {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .form-row-2col {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }

        .form-field-group {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .form-field-label {
          font-size: 12px;
          font-weight: 500;
          color: var(--text-secondary);
        }

        .form-control-input {
          width: 100%;
          height: 34px;
          padding: 0 12px;
          font-size: 13px;
          background-color: var(--bg-surface);
          color: var(--text-primary);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          outline: none;
        }

        .form-control-input:focus {
          border-color: var(--border-focus);
        }

        .form-textarea-control {
          width: 100%;
          padding: 8px 12px;
          font-size: 13px;
          background-color: var(--bg-surface);
          color: var(--text-primary);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          outline: none;
          font-family: inherit;
          resize: vertical;
        }

        .form-textarea-control:focus {
          border-color: var(--border-focus);
        }

        .form-select-control {
          padding: 8px 12px;
          font-size: 13px;
          background-color: var(--bg-surface);
          color: var(--text-primary);
          border: 1px solid var(--border-strong);
          border-radius: var(--radius-sm);
          outline: none;
        }

        .form-select-control:focus {
          border-color: var(--border-focus);
        }

        .form-error-banner {
          padding: 8px 12px;
          border-radius: var(--radius-xs);
          background-color: var(--color-danger-bg);
          border: 1px solid var(--color-danger-border);
          color: var(--color-danger);
          font-size: 12px;
        }

        /* ========================================================
           HIGH-TRUST FINTECH PUBLISH MODAL STYLING
           ======================================================== */
        .publish-modal-body {
          display: flex;
          flex-direction: column;
          gap: 16px;
          font-size: 13px;
          color: var(--text-secondary);
        }

        /* Pre-flight Release Section */
        .preflight-release-section {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .preflight-lead-desc {
          font-size: 12px;
          line-height: 1.5;
          color: var(--text-secondary);
          margin-bottom: 2px;
        }

        .preflight-checklist-grid {
          display: grid;
          grid-template-columns: 1fr;
          gap: 8px;
        }

        .checklist-item-card {
          display: flex;
          align-items: flex-start;
          gap: 12px;
          padding: 10px 12px;
          background-color: var(--bg-subtle);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-sm);
          transition: border-color var(--transition-fast);
        }

        .checklist-item-card:hover {
          border-color: var(--border-strong);
        }

        .check-icon-circle {
          width: 20px;
          height: 20px;
          border-radius: var(--radius-pill);
          background-color: var(--color-success-bg);
          color: var(--color-success);
          border: 1px solid var(--color-success-border);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 11px;
          font-weight: 700;
          flex-shrink: 0;
          margin-top: 1px;
        }

        .check-text-group {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .check-title {
          font-size: 12px;
          font-weight: 600;
          color: var(--text-primary);
        }

        .check-desc {
          font-size: 11px;
          color: var(--text-muted);
          line-height: 1.4;
        }

        .preflight-key-box {
          display: flex;
          flex-direction: column;
          gap: 8px;
          padding: 10px 12px;
          background-color: var(--bg-surface);
          border: 1px dashed var(--border-strong);
          border-radius: var(--radius-xs);
          font-size: 12px;
        }

        .key-box-row {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .key-box-label {
          color: var(--text-muted);
          font-weight: 500;
        }

        .key-box-mono {
          word-break: break-all;
          font-size: 11px;
          color: var(--text-primary);
        }

        .copy-mini-btn {
          background-color: var(--bg-subtle);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-xs);
          padding: 2px 7px;
          font-size: 11px;
          font-weight: 500;
          color: var(--color-primary);
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .copy-mini-btn:hover {
          border-color: var(--color-primary-border);
          background-color: var(--color-primary-subtle);
        }

        /* In-Flight Stepper */
        .publish-stepper-container {
          display: flex;
          flex-direction: column;
          gap: 16px;
          padding: 16px;
          background-color: var(--bg-subtle);
          border-radius: var(--radius-sm);
          border: 1px solid var(--border-subtle);
        }

        .stepper-lead-text {
          font-size: 13px;
          font-weight: 600;
          color: var(--text-primary);
          text-align: center;
        }

        .stepper-track {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .stepper-node {
          display: flex;
          align-items: flex-start;
          gap: 12px;
          padding: 10px 12px;
          background-color: var(--bg-surface);
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
          transition: all var(--transition-fast);
        }

        .step-active {
          border-color: var(--color-primary);
          box-shadow: 0 0 0 2px var(--color-primary-subtle);
        }

        .step-done {
          border-color: var(--color-success-border);
          background-color: var(--color-success-bg);
        }

        .step-waiting {
          opacity: 0.55;
        }

        .node-icon-circle {
          width: 24px;
          height: 24px;
          border-radius: var(--radius-pill);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          font-weight: 700;
          flex-shrink: 0;
          background-color: var(--bg-subtle);
          color: var(--text-muted);
        }

        .step-active .node-icon-circle {
          background-color: var(--color-primary);
          color: #ffffff;
          animation: pulse-dot 1.5s infinite ease-in-out;
        }

        .step-done .node-icon-circle {
          background-color: var(--color-success);
          color: #ffffff;
        }

        .node-content {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .node-title {
          font-size: 12px;
          font-weight: 600;
          color: var(--text-primary);
        }

        .node-desc {
          font-size: 11px;
          color: var(--text-muted);
        }

        /* Intent Recovery Card */
        .intent-recovery-card {
          background-color: rgba(79, 70, 229, 0.04);
          border: 1px solid rgba(79, 70, 229, 0.35);
          border-radius: var(--radius-sm);
          padding: 16px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .recovery-header-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          flex-wrap: wrap;
        }

        .recovery-badge {
          font-size: 10px;
          font-weight: 700;
          color: #4F46E5;
          background-color: rgba(79, 70, 229, 0.12);
          padding: 2px 7px;
          border-radius: var(--radius-xs);
          letter-spacing: 0.03em;
        }

        .recovery-timestamp {
          font-size: 11px;
          color: var(--text-muted);
        }

        .recovery-title {
          font-size: 13px;
          font-weight: 700;
          color: var(--text-primary);
          margin: 0;
        }

        .recovery-explanation {
          font-size: 12px;
          color: var(--text-secondary);
          line-height: 1.45;
          margin: 0;
        }

        .recovery-meta-strip {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 10px 12px;
          background-color: var(--bg-surface);
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
          font-size: 11px;
        }

        .meta-kv {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .meta-kv .k {
          color: var(--text-muted);
        }

        .meta-kv .v {
          color: var(--text-primary);
          font-weight: 600;
        }

        .rev-diff-tag {
          color: var(--color-warning);
          font-size: 11px;
        }

        /* Digital Release Certificate */
        .digital-release-certificate {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .certificate-emblem-header {
          display: flex;
          align-items: flex-start;
          gap: 14px;
          padding: 16px;
          background: linear-gradient(135deg, rgba(5, 150, 105, 0.08) 0%, rgba(15, 118, 110, 0.12) 100%);
          border: 1px solid var(--color-success-border);
          border-radius: var(--radius-md);
        }

        .certificate-seal-badge {
          width: 44px;
          height: 44px;
          border-radius: var(--radius-pill);
          background-color: var(--bg-surface);
          border: 2px solid var(--color-success);
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: var(--shadow-sm);
          flex-shrink: 0;
        }

        .certificate-titles {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .certificate-micro-tag {
          font-size: 10px;
          font-weight: 700;
          color: var(--color-success);
          letter-spacing: 0.06em;
        }

        .certificate-main-title {
          font-size: 18px;
          font-weight: 700;
          color: var(--text-primary);
          margin: 0;
        }

        .certificate-sub {
          font-size: 12px;
          color: var(--text-secondary);
        }

        .certificate-details-card {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 14px;
          background-color: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-sm);
          font-size: 12px;
        }

        .cert-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--border-subtle);
        }

        .cert-label {
          color: var(--text-muted);
          font-weight: 500;
        }

        .cert-val {
          color: var(--text-primary);
          font-weight: 600;
        }

        .cert-mono {
          font-family: var(--font-mono);
          font-size: 11px;
        }

        .cert-row-digest {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--border-subtle);
        }

        .cert-digest-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        .cert-copy-btn {
          background-color: var(--color-primary-subtle);
          border: 1px solid var(--color-primary-border);
          border-radius: var(--radius-xs);
          padding: 2px 8px;
          font-size: 11px;
          font-weight: 600;
          color: var(--color-primary);
          cursor: pointer;
          transition: all var(--transition-fast);
        }

        .cert-copy-btn:hover {
          background-color: var(--color-primary);
          color: #ffffff;
        }

        .cert-hash-box {
          padding: 8px 10px;
          background-color: var(--bg-subtle);
          border-radius: var(--radius-xs);
          border: 1px solid var(--border-subtle);
          word-break: break-all;
          color: var(--color-primary);
          font-weight: 600;
          font-size: 11px;
        }

        .publish-feedback-banner {
          padding: 8px 12px;
          border-radius: var(--radius-xs);
          background-color: var(--color-warning-bg);
          border: 1px solid var(--color-warning-border);
          color: var(--color-warning);
          font-size: 12px;
        }

        .load-more-container {
          margin-top: 16px;
          display: flex;
          justify-content: center;
        }

        /* Zero CLS Skeleton Cards */
        .skeleton-cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 16px;
        }

        .skeleton-agent-card {
          background-color: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-lg);
          padding: 16px;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .skeleton-card-top {
          display: flex;
          gap: 12px;
          align-items: center;
        }

        .skeleton-circle-avatar {
          width: 42px;
          height: 42px;
          border-radius: var(--radius-md);
        }

        .skeleton-title-lines {
          display: flex;
          flex-direction: column;
          gap: 6px;
          flex: 1;
        }

        .skeleton-line-title {
          width: 60%;
          height: 14px;
        }

        .skeleton-line-slug {
          width: 40%;
          height: 10px;
        }

        .skeleton-card-middle {
          display: flex;
          gap: 8px;
        }

        .skeleton-badge {
          width: 80px;
          height: 20px;
          border-radius: var(--radius-pill);
        }

        .skeleton-badge-sm {
          width: 50px;
          height: 20px;
          border-radius: var(--radius-xs);
        }

        .skeleton-card-bottom {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          padding-top: 10px;
          border-top: 1px solid var(--border-subtle);
        }

        .skeleton-btn {
          width: 80px;
          height: 28px;
          border-radius: var(--radius-xs);
        }

        .skeleton-btn-primary {
          width: 74px;
          height: 28px;
          border-radius: var(--radius-xs);
        }
      `}</style>
    </div>
  );
}
