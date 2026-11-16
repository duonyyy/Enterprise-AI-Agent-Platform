"use client";

import React, { useState, useEffect, useMemo, useCallback, useRef, useLayoutEffect } from "react";
import { useRouter } from "next/navigation";
import {
  PageHeader,
  DataTable,
  StatusBadge,
  Button,
  FilterBar,
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
  ApiClientError,
} from "@enterprise/api-client";

import {
  AgentRequestScope,
  AgentValidationScope,
  isValidationResultStale,
  mergeAgentDraftEdits,
} from "../../../../../lib/agent-draft-state";

type ViewState = "ready" | "loading" | "empty" | "error" | "forbidden" | "stale";

export default function AgentsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";
  const router = useRouter();
  const requestScope = useRef(new AgentRequestScope());
  const editorScope = useRef(new AgentRequestScope());
  const validationScope = useRef(new AgentValidationScope());
  useLayoutEffect(() => {
    const scope = requestScope.current;
    const editor = editorScope.current;
    const validation = validationScope.current;
    scope.reset();
    return () => {
      scope.invalidate();
      editor.invalidate();
      validation.invalidate();
    };
  }, [workspaceId]);

  // Data & State
  const [agents, setAgents] = useState<AgentDraftMetadata[]>([]);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [errorStatus, setErrorStatus] = useState<number | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  // Filters & Search
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
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
  const [newMode, setNewMode] = useState<"fixed_plan" | "planner_based" | "react">("fixed_plan");
  const [newMaxSteps, setNewMaxSteps] = useState(10);
  const [newMaxWallTime, setNewMaxWallTime] = useState(180);
  const [newMaxTokens, setNewMaxTokens] = useState(16000);
  const [createError, setCreateError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Editor Drawer / Modal
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [activeDraft, setActiveDraft] = useState<AgentDraft | null>(null);
  const [editorInstructions, setEditorInstructions] = useState("");
  const [editorModelRef, setEditorModelRef] = useState("");
  const [editorMode, setEditorMode] = useState<"fixed_plan" | "planner_based" | "react">("fixed_plan");
  const [editorMaxSteps, setEditorMaxSteps] = useState(10);
  const [editorMaxWallTime, setEditorMaxWallTime] = useState(180);
  const [editorMaxTokens, setEditorMaxTokens] = useState(16000);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflictState, setConflictState] = useState<{
    message: string;
    serverRevision?: number;
  } | null>(null);

  // Validation State (04-B)
  const [isValidating, setIsValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<AgentValidationResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [hasEditsSinceValidation, setHasEditsSinceValidation] = useState(false);
  const invalidateValidation = useCallback(() => {
    validationScope.current.invalidate();
    setIsValidating(false);
    setValidationError(null);
    setHasEditsSinceValidation(true);
  }, []);

  const closeEditor = () => {
    editorScope.current.invalidate();
    invalidateValidation();
    setIsEditorOpen(false);
    setActiveDraft(null);
    setConflictState(null);
    setSaveError(null);
    setValidationResult(null);
    setHasEditsSinceValidation(false);
  };

  const validationIsStale = validationResult !== null && (
    validationResult.agent_id !== activeDraft?.agent_id ||
    isValidationResultStale(validationResult.revision, activeDraft?.revision ?? 1, hasEditsSinceValidation)
  );
  const validationReady = validationResult?.ready === true && !validationIsStale;

  // Publish Dialog
  const [publishTargetAgent, setPublishTargetAgent] = useState<AgentDraftMetadata | null>(null);
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);

  // Notification Toast
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  }, []);

  // Request cancellation on workspace switch
  const abortControllerRef = useRef<AbortController | null>(null);

  // Fetch agents from API (no seed/mock fallback)
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
            `/login?error=session_expired&return_to=${encodeURIComponent(window.location.pathname)}`
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
          typeof err.body === "object" && err.body !== null && "detail" in err.body
            ? (err.body as { detail: { message?: string } }).detail?.message || err.message
            : err.message
        );
      } else {
        setErrorMessage(err instanceof Error ? err.message : "Không thể kết nối tới máy chủ.");
      }
      setAgents([]);
      setViewState("error");
    } finally {
      if (scope.isCurrent() && !controller.signal.aborted) setIsRefreshing(false);
    }
  }, [workspaceId, router]);

  // Handle workspace change: reset state and fetch fresh
  useEffect(() => {
    setAgents([]);
    setIsCreateModalOpen(false);
    setIsSubmitting(false);
    setIsSaving(false);
    setPublishTargetAgent(null);
    setIsPublishing(false);
    setPublishMessage(null);
    setToastMessage(null);
    setCurrentPage(1);
    setNextCursor(null);
    setIsLoadingMore(false);
    setActiveDraft(null);
    setIsEditorOpen(false);
    setConflictState(null);
    setSaveError(null);
    setCreateError("");
    fetchAgents();

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [fetchAgents, workspaceId]);

  const loadMoreAgents = async () => {
    if (!nextCursor || isLoadingMore) return;
    const scope = requestScope.current.capture();
    setIsLoadingMore(true);
    try {
      const page = await defaultApiClient.listAgents(workspaceId, {
        cursor: nextCursor, signal: scope.signal,
      });
      if (!scope.isCurrent()) return;
      setAgents((previous) => {
        const known = new Set(previous.map((agent) => agent.agent_id));
        return [...previous, ...page.items.filter((agent) => !known.has(agent.agent_id))];
      });
      setNextCursor(page.next_cursor);
    } catch {
      if (scope.isCurrent()) showToast("Không thể tải thêm tác tử. Hãy thử lại.");
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
      setCreateError("Định danh Slug chỉ được chứa chữ thường, số, gạch ngang và gạch dưới.");
      return;
    }
    if (!trimmedInstructions) {
      setCreateError("Vui lòng nhập chỉ lệnh Persona (Persona Instructions) cho Agent.");
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
      const created = await defaultApiClient.createAgentDraft(workspaceId, {
        display_name: trimmedName,
        slug: trimmedSlug,
        spec: fullSpec,
      }, scope.signal);
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
      showToast(`Đã tạo bản nháp Tác tử "${meta.display_name}" thành công (revision ${meta.revision}).`);
      if (viewState === "empty") setViewState("ready");
    } catch (err: unknown) {
      if (!scope.isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(window.location.pathname)}`
          );
          return;
        }
        if (err.status === 409) {
          setCreateError(`Định danh slug "${trimmedSlug}" đã tồn tại trong Workspace này.`);
          return;
        }
        if (err.status === 403) {
          setCreateError("Từ chối truy cập: Bạn không có quyền khởi tạo Tác tử (cần vai trò Developer, Admin, hoặc Owner).");
          return;
        }
        if (err.status === 422) {
          const detail = typeof err.body === "object" && err.body !== null && "detail" in err.body
            ? (err.body as { detail: { message?: string } }).detail?.message
            : undefined;
          setCreateError(`Dữ liệu không hợp lệ: ${detail || "AgentSpec không đúng định dạng chuẩn."}`);
          return;
        }
        setCreateError(err.message || "Lỗi máy chủ khi tạo bản nháp Agent.");
      } else {
        setCreateError(err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định.");
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

    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const draft = await defaultApiClient.getAgentDraft(workspaceId, agent.agent_id, editor.signal);
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      setActiveDraft(draft);
      const spec = draft.spec || {};
      const persona = (spec.persona as { instructions?: string }) || {};
      const limits = (spec.limits as { maxSteps?: number; maxWallTimeSeconds?: number; maxTotalTokens?: number }) || {};

      setEditorInstructions(persona.instructions || "");
      setEditorModelRef(String(spec.modelRef || ""));
      setEditorMode(
        (spec.mode as "fixed_plan" | "planner_based" | "react") || "fixed_plan"
      );
      setEditorMaxSteps(Number(limits.maxSteps || 10));
      setEditorMaxWallTime(Number(limits.maxWallTimeSeconds || 180));
      setEditorMaxTokens(Number(limits.maxTotalTokens || 16000));
      setIsEditorOpen(true);
    } catch (err: unknown) {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      if (err instanceof ApiClientError && err.status === 404) {
        showToast("Tác tử không tồn tại trong Workspace này.");
        fetchAgents();
        return;
      }
      showToast("Không thể tải chi tiết bản nháp Agent.");
    }
  };

  // Reload latest draft from server after 412 conflict
  const handleReloadLatestDraft = async () => {
    if (!activeDraft) return;
    invalidateValidation();
    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const freshDraft = await defaultApiClient.getAgentDraft(workspaceId, activeDraft.agent_id, editor.signal);
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      setActiveDraft(freshDraft);
      const spec = freshDraft.spec || {};
      const persona = (spec.persona as { instructions?: string }) || {};
      const limits = (spec.limits as { maxSteps?: number; maxWallTimeSeconds?: number; maxTotalTokens?: number }) || {};

      setEditorInstructions(persona.instructions || "");
      setEditorModelRef(String(spec.modelRef || ""));
      setEditorMode(
        (spec.mode as "fixed_plan" | "planner_based" | "react") || "fixed_plan"
      );
      setEditorMaxSteps(Number(limits.maxSteps || 10));
      setEditorMaxWallTime(Number(limits.maxWallTimeSeconds || 180));
      setEditorMaxTokens(Number(limits.maxTotalTokens || 16000));

      setConflictState(null);
      setSaveError(null);
      showToast(`Đã đồng bộ bản nháp mới nhất (revision ${freshDraft.revision}) từ máy chủ.`);
    } catch {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      showToast("Không thể tải lại bản nháp từ máy chủ.");
    }
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
    const validation = validationScope.current.begin(activeDraft.agent_id, activeDraft.revision, updatedSpec);
    try {
      const result = await defaultApiClient.validateAgentDraft(
        workspaceId,
        activeDraft.agent_id,
        { spec: updatedSpec },
        validation.signal
      );
      if (!scope.isCurrent() || !editor.isCurrent() || !validation.isCurrent()) return;
      if (!validation.matchesResponse(result)) {
        throw new Error("Kết quả kiểm tra không khớp Agent hoặc revision hiện tại. Hãy tải lại bản nháp.");
      }
      setValidationResult(result);
      setHasEditsSinceValidation(false);
      if (result.ready) {
        showToast("Cấu hình hợp lệ và sẵn sàng công bố.");
      } else {
        showToast("Cấu hình chưa sẵn sàng (có phát hiện lỗi phụ thuộc).");
      }
    } catch (err: unknown) {
      if (!scope.isCurrent() || !editor.isCurrent() || !validation.isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(window.location.pathname)}`
          );
          return;
        }
        setValidationError(err.message || "Lỗi kiểm tra cấu hình.");
      } else {
        setValidationError(err instanceof Error ? err.message : "Lỗi không xác định khi kiểm tra cấu hình.");
      }
    } finally {
      if (scope.isCurrent() && editor.isCurrent() && validation.isCurrent()) setIsValidating(false);
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
      return;
    }

    setIsSaving(true);
    setSaveError(null);

    const updatedSpec = mergeAgentDraftEdits(activeDraft.spec, {
      mode: editorMode, modelRef: editorModelRef.trim(), instructions: trimmedInstructions,
      maxSteps: Number(editorMaxSteps), maxWallTimeSeconds: Number(editorMaxWallTime),
      maxTotalTokens: Number(editorMaxTokens),
    });

    const scope = requestScope.current.capture();
    const editor = editorScope.current.capture();
    try {
      const saved = await defaultApiClient.replaceAgentDraftSpec(
        workspaceId,
        activeDraft.agent_id,
        { spec: updatedSpec },
        activeDraft.revision, // If-Match: "<revision>"
        editor.signal
      );
      if (!scope.isCurrent() || !editor.isCurrent()) return;

      setActiveDraft(saved);
      setConflictState(null);
      setSaveError(null);
      invalidateValidation();

      // Update list
      setAgents((prev) =>
        prev.map((ag) =>
          ag.agent_id === saved.agent_id
            ? { ...ag, revision: saved.revision }
            : ag
        )
      );

      showToast(`Đã lưu thay đổi thành công (revision ${saved.revision}).`);
    } catch (err: unknown) {
      if (!scope.isCurrent() || !editor.isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          // Stale Revision Conflict
          setConflictState({
            message:
              "Xung đột sửa đổi (HTTP 412 Stale Revision): Bản nháp đã được cập nhật bởi một phiên làm việc khác. Thay đổi chưa lưu của bạn được giữ lại nguyên vẹn.",
            serverRevision: (activeDraft.revision || 1) + 1,
          });
          return;
        }
        if (err.status === 409) {
          setSaveError("Bản nháp này đã bị lưu trữ (archived) hoặc xảy ra xung đột tài nguyên.");
          return;
        }
        if (err.status === 403) {
          setSaveError("Từ chối truy cập: Bạn không có quyền chỉnh sửa Tác tử này.");
          return;
        }
        if (err.status === 422) {
          const detail = typeof err.body === "object" && err.body !== null && "detail" in err.body
            ? (err.body as { detail: { message?: string } }).detail?.message
            : undefined;
          setSaveError(`Cấu trúc spec không hợp lệ: ${detail || "Lỗi kiểm tra tính hợp lệ của schema."}`);
          return;
        }
        setSaveError(err.message || "Lỗi máy chủ khi cập nhật bản nháp.");
      } else {
        setSaveError(err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định.");
      }
    } finally {
      if (scope.isCurrent() && editor.isCurrent()) setIsSaving(false);
    }
  };

  // Submit Publish Agent (Fail-closed in 04-A per ADR-022)
  const handleConfirmPublish = async () => {
    if (!publishTargetAgent) return;
    setIsPublishing(true);
    setPublishMessage(null);

    const scope = requestScope.current.capture();
    try {
      await defaultApiClient.publishAgent(workspaceId, publishTargetAgent.agent_id, scope.signal);
      if (!scope.isCurrent()) return;
      showToast(`Đã xuất bản Tác tử "${publishTargetAgent.display_name}".`);
      setPublishTargetAgent(null);
    } catch (err: unknown) {
      if (!scope.isCurrent()) return;
      if (err instanceof ApiClientError && err.status === 503) {
        setPublishMessage(
          "Tính năng xuất bản phiên bản bất biến (Publish) bị vô hiệu hóa an toàn trong Bước 04-A và sẽ được kích hoạt tại Bước 04-C theo thiết kế ADR-022. Bản nháp không bị thay đổi."
        );
        return;
      }
      if (err instanceof ApiClientError && err.status === 403) {
        setPublishMessage("Từ chối truy cập: Bạn không có quyền xuất bản Tác tử.");
        return;
      }
      setPublishMessage(err instanceof Error ? err.message : "Dịch vụ xuất bản tạm thời không khả dụng.");
    } finally {
      if (scope.isCurrent()) setIsPublishing(false);
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

  const totalPages = Math.max(1, Math.ceil(filteredAgents.length / itemsPerPage));
  const paginatedAgents = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return filteredAgents.slice(start, start + itemsPerPage);
  }, [filteredAgents, currentPage, itemsPerPage]);

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

  // Table Columns Definition
  const columns: Column<AgentDraftMetadata>[] = [
    {
      key: "display_name",
      header: "AI Agent & Slug",
      width: "40%",
      render: (item) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "13px" }}>
              {item.display_name}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--text-muted)",
                backgroundColor: "var(--bg-subtle)",
                padding: "1px 5px",
                borderRadius: "var(--radius-xs)",
              }}
            >
              {item.slug}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "revision",
      header: "Revision",
      width: "16%",
      render: (item) => (
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-primary)",
              backgroundColor: "var(--color-primary-subtle)",
              padding: "2px 6px",
              borderRadius: "var(--radius-xs)",
              border: "1px solid var(--color-primary-border)",
            }}
          >
            rev {item.revision}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "16%",
      render: (item) => (
        <StatusBadge status={item.archived_at ? "ARCHIVED" : "DRAFT"} />
      ),
    },
    {
      key: "created_at",
      header: "Created At",
      width: "16%",
      render: (item) => (
        <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
          {formatDate(item.created_at)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "Actions",
      width: "12%",
      align: "right",
      render: (item) => (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "6px" }}>
          <Button
            variant="outline"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              handleOpenEditor(item);
            }}
            title="Edit Agent Draft"
          >
            Chỉnh sửa
          </Button>
          <Button
            variant="copper"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              setPublishMessage(null);
              setPublishTargetAgent(item);
            }}
            title="Publish (Disabled in 04-A)"
            style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <BrandIcon name="action-publish" size={13} color="currentColor" />
            Publish
          </Button>
        </div>
      ),
    },
  ];

  const filterOptions = [
    { value: "ALL", label: "Tất cả trạng thái" },
    { value: "DRAFT", label: "Bản nháp (Draft)" },
    { value: "ARCHIVED", label: "Đã lưu trữ (Archived)" },
  ];

  return (
    <div>
      {/* Toast Notification */}
      {toastMessage && (
        <div
          role="status"
          style={{
            position: "fixed",
            bottom: "24px",
            right: "24px",
            backgroundColor: "var(--bg-surface-elevated)",
            color: "var(--text-primary)",
            padding: "12px 18px",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--border-strong)",
            boxShadow: "var(--shadow-lg)",
            fontSize: "13px",
            display: "flex",
            alignItems: "center",
            gap: "10px",
            zIndex: 1000,
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-success)" strokeWidth="2.5">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <span>{toastMessage}</span>
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
            variant="primary"
            size="md"
            onClick={() => {
              setCreateError("");
              setIsCreateModalOpen(true);
            }}
            leftIcon={
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            }
          >
            + Create Agent
          </Button>
        }
      />

      {/* Stale Banner when state is stale */}
      {viewState === "stale" && (
        <StaleBanner
          message="Dữ liệu hiển thị có thể chưa đồng bộ do có sửa đổi phiên bản mới hơn trên máy chủ khác."
          onRefresh={fetchAgents}
          isRefreshing={isRefreshing}
        />
      )}

      {/* Filter and Search Bar */}
      {viewState !== "error" && viewState !== "forbidden" && (
        <FilterBar
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          selectedFilter={statusFilter}
          onFilterChange={setStatusFilter}
          filterOptions={filterOptions}
          onRefresh={fetchAgents}
          isRefreshing={isRefreshing}
          totalCount={agents.length}
          filteredCount={filteredAgents.length}
        />
      )}

      {/* 6 UI States Rendering */}
      {viewState === "loading" && <LoadingSkeleton rows={5} columns={5} />}

      {viewState === "error" && (
        <ErrorState
          title="Không thể tải danh sách Tác tử AI"
          message={errorMessage || "Đã xảy ra lỗi khi kết nối tới máy chủ Control Plane."}
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
          onContactAdmin={() => showToast("Đã gửi thông báo yêu cầu cấp quyền đến Quản trị viên.")}
        />
      )}

      {viewState === "empty" && (
        <EmptyState
          title="Chưa có Tác tử AI nào trong không gian này"
          description="Bắt đầu bằng cách tạo bản nháp tác tử đầu tiên với chỉ lệnh persona và cấu hình tài nguyên."
          actionLabel="+ Tạo Agent đầu tiên"
          onAction={() => {
            setCreateError("");
            setIsCreateModalOpen(true);
          }}
        />
      )}

      {(viewState === "ready" || viewState === "stale") && (
        <>
          <div className="agent-desktop-table">
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
                  onAction={() => {
                    setSearchQuery("");
                    setStatusFilter("ALL");
                  }}
                />
              }
            />
          </div>

          <Pagination
            currentPage={currentPage}
            totalPages={totalPages}
            onPageChange={setCurrentPage}
            totalItems={filteredAgents.length}
            itemsPerPage={itemsPerPage}
          />
          {nextCursor && (
            <Button disabled={isLoadingMore} onClick={loadMoreAgents}>
              {isLoadingMore ? "Đang tải…" : "Tải thêm tác tử"}
            </Button>
          )}
        </>
      )}

      {/* Modal: Create Agent Draft */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        title="Tạo Bản nháp Tác tử AI (Create Agent Draft)"
        description="Khởi tạo bản nháp với cấu hình AgentSpec đầy đủ. Ràng buộc mô hình và công cụ sẽ được xác thực tại Bước 04-B."
        maxWidth="540px"
        footer={
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={() => setIsCreateModalOpen(false)}
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
          style={{ display: "flex", flexDirection: "column", gap: "14px" }}
        >
          <Input
            label="Tên hiển thị Agent (Display Name)"
            placeholder="Ví dụ: Trợ lý Soạn thảo Báo giá"
            value={newAgentName}
            onChange={(e) => handleNameChange(e.target.value)}
            required
            autoFocus
          />

          <Input
            label="Định danh Kỹ thuật (Technical Slug)"
            placeholder="tro-ly-soan-thao-bao-gia"
            value={newAgentSlug}
            onChange={(e) => setNewAgentSlug(e.target.value)}
            helperText="Chữ thường không dấu, số và gạch ngang (bất biến sau khi tạo)."
            required
          />

          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <label
              htmlFor="agent-instructions"
              style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-secondary)" }}
            >
              Chỉ lệnh Persona (Instructions) *
            </label>
            <textarea
              id="agent-instructions"
              rows={4}
              placeholder="Nhập chỉ lệnh hoạt động, mục tiêu và ranh giới an toàn cho tác tử..."
              value={newInstructions}
              onChange={(e) => setNewInstructions(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "8px 12px",
                fontSize: "13px",
                backgroundColor: "var(--bg-surface)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-strong)",
                borderRadius: "var(--radius-sm)",
                outline: "none",
                fontFamily: "inherit",
                resize: "vertical",
              }}
            />
          </div>

          <Input
            label="Tham chiếu Mô hình (Model Reference)"
            placeholder="approved-general@2"
            value={newModelRef}
            onChange={(e) => setNewModelRef(e.target.value)}
            helperText="Tham chiếu định danh cấu hình Model (sẽ được đối soát ở Bước 04-B)."
            required
          />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label htmlFor="agent-mode" style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-secondary)" }}>
                Chế độ thực thi (Mode)
              </label>
              <select
                id="agent-mode"
                value={newMode}
                onChange={(e) => setNewMode(e.target.value as "fixed_plan" | "planner_based" | "react")}
                style={{
                  padding: "8px 12px",
                  fontSize: "13px",
                  backgroundColor: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)",
                  outline: "none",
                }}
              >
                <option value="fixed_plan">fixed_plan</option>
                <option value="planner_based">planner_based</option>
                <option value="react">react</option>
              </select>
            </div>

            <Input
              label="Số bước tối đa (Max Steps)"
              type="number"
              min={1}
              max={50}
              value={newMaxSteps}
              onChange={(e) => setNewMaxSteps(Number(e.target.value))}
              required
            />
          </div>

          {createError && (
            <div
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-xs)",
                backgroundColor: "var(--color-danger-bg)",
                border: "1px solid var(--color-danger-border)",
                color: "var(--color-danger)",
                fontSize: "12px",
              }}
            >
              {createError}
            </div>
          )}
        </form>
      </Modal>

      {/* Modal: Edit Agent Draft (Optimistic Concurrency Control) */}
      <Modal
        isOpen={isEditorOpen}
        onClose={closeEditor}
        title={`Chỉnh sửa Bản nháp: ${activeDraft?.display_name || ""}`}
        description={`Định danh: ${activeDraft?.slug} | Phiên bản sửa đổi hiện tại: revision ${activeDraft?.revision}`}
        maxWidth="600px"
        footer={
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={closeEditor}
              disabled={isSaving || isValidating}
            >
              Đóng
            </Button>
            <Button
              variant="outline"
              size="md"
              type="button"
              onClick={handleValidateDraft}
              isLoading={isValidating}
              disabled={isSaving}
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
            >
              Lưu thay đổi (Save Spec)
            </Button>
          </>
        }
      >
        <form
          id="edit-agent-form"
          onSubmit={handleSaveDraftSpec}
          style={{ display: "flex", flexDirection: "column", gap: "14px" }}
        >
          {/* Validation Diagnostics & Readiness Box (04-B) */}
          {validationError && (
            <div
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-xs)",
                backgroundColor: "var(--color-danger-bg)",
                border: "1px solid var(--color-danger-border)",
                color: "var(--color-danger)",
                fontSize: "12px",
              }}
            >
              {validationError}
            </div>
          )}

          {validationResult && (
            <div
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-sm)",
                backgroundColor: validationReady ? "var(--color-success-bg, #f0fdf4)" : "var(--color-warning-bg)",
                border: `1px solid ${validationReady ? "var(--color-success-border, #bbf7d0)" : "var(--color-warning-border)"}`,
                color: "var(--text-primary)",
                fontSize: "12px",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
              }}
            >
              {validationIsStale && (
                <div style={{ color: "var(--color-warning)", fontWeight: 600 }}>
                  ⚠️ Cấu hình đã thay đổi sau lần kiểm tra gần nhất (Kết quả bên dưới có thể đã cũ).
                </div>
              )}
              <div style={{ fontWeight: 600, color: validationReady ? "var(--color-success, #16a34a)" : "var(--color-danger)" }}>
                {validationIsStale
                  ? "Cấu hình hiện tại chưa được kiểm tra. Kết quả trước đã cũ."
                  : validationReady
                  ? `✓ Cấu hình hợp lệ và sẵn sàng công bố (Đã đối chiếu dependencies tại revision ${validationResult.revision}).`
                  : `✕ Cấu hình chưa sẵn sàng công bố (Phát hiện ${validationResult.diagnostics.length} vấn đề):`}
              </div>
              {!validationResult.ready && validationResult.diagnostics.length > 0 && (
                <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                  {validationResult.diagnostics.map((d, idx) => (
                    <li key={idx} style={{ marginTop: "4px" }}>
                      <strong>[{d.field}]</strong> <span style={{ color: "var(--text-secondary)" }}>({d.code})</span>: {d.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Conflict 412 Banner */}
          {conflictState && (
            <div
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "var(--color-warning-bg)",
                border: "1px solid var(--color-warning-border)",
                color: "var(--text-primary)",
                fontSize: "12px",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
              }}
            >
              <div style={{ fontWeight: 600, color: "var(--color-warning)" }}>
                ⚠️ {conflictState.message}
              </div>
              <div style={{ display: "flex", gap: "8px", marginTop: "4px" }}>
                <Button
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={handleReloadLatestDraft}
                >
                  Tải lại bản mới nhất từ máy chủ
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => setConflictState(null)}
                >
                  Giữ lại thay đổi hiện tại để sao chép
                </Button>
              </div>
            </div>
          )}

          {saveError && (
            <div
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-xs)",
                backgroundColor: "var(--color-danger-bg)",
                border: "1px solid var(--color-danger-border)",
                color: "var(--color-danger)",
                fontSize: "12px",
              }}
            >
              {saveError}
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <Input
              label="Technical Slug (Bất biến)"
              value={activeDraft?.slug || ""}
              disabled
              helperText="Slug không thể thay đổi sau khi tạo."
            />
            <Input
              label="Phiên bản sửa đổi (Revision)"
              value={`rev ${activeDraft?.revision || 1}`}
              disabled
              helperText="Được quản lý tự động bởi CAS."
            />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <label
              htmlFor="edit-instructions"
              style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-secondary)" }}
            >
              Chỉ lệnh Persona (Persona Instructions) *
            </label>
            <textarea
              id="edit-instructions"
              rows={5}
              value={editorInstructions}
              onChange={(e) => {
                setEditorInstructions(e.target.value);
                invalidateValidation();
              }}
              required
              style={{
                width: "100%",
                padding: "8px 12px",
                fontSize: "13px",
                backgroundColor: "var(--bg-surface)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-strong)",
                borderRadius: "var(--radius-sm)",
                outline: "none",
                fontFamily: "inherit",
                resize: "vertical",
              }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <Input
              label="Tham chiếu Mô hình (Model Reference)"
              value={editorModelRef}
              onChange={(e) => {
                setEditorModelRef(e.target.value);
                invalidateValidation();
              }}
              required
            />
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label htmlFor="edit-mode" style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-secondary)" }}>
                Chế độ thực thi (Mode)
              </label>
              <select
                id="edit-mode"
                value={editorMode}
                onChange={(e) => {
                  setEditorMode(e.target.value as "fixed_plan" | "planner_based" | "react");
                  invalidateValidation();
                }}
                style={{
                  padding: "8px 12px",
                  fontSize: "13px",
                  backgroundColor: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)",
                  outline: "none",
                }}
              >
                <option value="fixed_plan">fixed_plan</option>
                <option value="planner_based">planner_based</option>
                <option value="react">react</option>
              </select>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
            <Input
              label="Max Steps"
              type="number"
              min={1}
              max={50}
              value={editorMaxSteps}
              onChange={(e) => {
                setEditorMaxSteps(Number(e.target.value));
                invalidateValidation();
              }}
              required
            />
            <Input
              label="Max Wall Time (s)"
              type="number"
              min={1}
              value={editorMaxWallTime}
              onChange={(e) => {
                setEditorMaxWallTime(Number(e.target.value));
                invalidateValidation();
              }}
              required
            />
            <Input
              label="Max Tokens"
              type="number"
              min={1}
              value={editorMaxTokens}
              onChange={(e) => {
                setEditorMaxTokens(Number(e.target.value));
                invalidateValidation();
              }}
              required
            />
          </div>
        </form>
      </Modal>

      {/* Modal: Publish Notice (Fail-closed in 04-A) */}
      <Modal
        isOpen={Boolean(publishTargetAgent)}
        onClose={() => {
          setPublishTargetAgent(null);
          setPublishMessage(null);
        }}
        title="Xuất bản Phiên bản Bất biến (Publish)"
        description={`Tác tử: ${publishTargetAgent?.display_name} (${publishTargetAgent?.slug})`}
        maxWidth="500px"
        footer={
          <>
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={() => {
                setPublishTargetAgent(null);
                setPublishMessage(null);
              }}
            >
              Đóng
            </Button>
            <Button
              variant="copper"
              size="md"
              type="button"
              onClick={handleConfirmPublish}
              isLoading={isPublishing}
            >
              Thực hiện xuất bản
            </Button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px", color: "var(--text-secondary)" }}>
          <p style={{ margin: 0 }}>
            Theo quyết định kiến trúc <strong>ADR-022</strong> và <strong>D04-02</strong>, việc công bố phiên bản bất biến yêu cầu cơ chế khóa liên kết và sổ cái biên nhận giao dịch (sẽ được triển khai tại <strong>Bước 04-C</strong>).
          </p>
          <div
            style={{
              padding: "10px 12px",
              backgroundColor: "var(--bg-subtle)",
              borderRadius: "var(--radius-xs)",
              border: "1px solid var(--border-subtle)",
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              color: "var(--text-muted)",
            }}
          >
            Trạng thái hiện tại: STEP-04A Draft CRUD ONLY. Publish bị vô hiệu hóa an toàn (fail-closed, HTTP 503 capability_unavailable).
          </div>
          {publishMessage && (
            <div
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-xs)",
                backgroundColor: "var(--color-warning-bg)",
                border: "1px solid var(--color-warning-border)",
                color: "var(--color-warning)",
                fontSize: "12px",
              }}
            >
              {publishMessage}
            </div>
          )}
        </div>
      </Modal>

      <style>{`
        .agent-desktop-table {
          display: block;
        }
      `}</style>
    </div>
  );
}
