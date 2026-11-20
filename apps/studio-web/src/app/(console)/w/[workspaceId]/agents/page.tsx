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
import { useWorkspaceSession } from "../workspace-context";
import {
  PageHeader,
  StatusBadge,
  Button,
  StaleBanner,
} from "@enterprise/ui";
import {
  defaultApiClient,
  AgentDraftMetadata,
  AgentDraft,
  PublishResponse,
  ApiClientError,
  AgentVersionMetadata,
  AgentPublication,
  AgentEnvironment,
  CreateAgentDraftDTO,
} from "@enterprise/api-client";

import {
  AgentPromotionScope,
  AgentPublishScope,
  AgentRequestScope,
  clearPendingPublishIntent,
  getPendingPublishIntent,
  savePendingPublishIntent,
  generateSecureIdempotencyKey,
} from "../../../../../lib/agent-draft-state";

import styles from "./agents.module.css";
import {
  AgentList,
  ViewState,
  ViewMode,
  StarterTemplate,
} from "./agent-list";
import { AgentCreateModal } from "./agent-create-modal";
import { AgentWorkshop } from "./agent-workshop";
import {
  AgentPublishModal,
  AgentPromoteModal,
} from "./agent-release-modal";

type ToastType = "success" | "error" | "info";

interface ToastNotification {
  id: number;
  message: string;
  type: ToastType;
}

export default function AgentsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";
  const router = useRouter();
  const { currentUser } = useWorkspaceSession();
  const actorId = currentUser.user_id;

  // Scopes (R04C-04, R04C-05, Concurrency Isolation, 04-D)
  const requestScope = useRef(new AgentRequestScope());
  const listScope = useRef(new AgentRequestScope());
  const editorScope = useRef(new AgentRequestScope());
  const publishScope = useRef(new AgentPublishScope());
  const promoteScope = useRef(new AgentPromotionScope());
  const promoteDialogScope = useRef(new AgentRequestScope());
  const historyScope = useRef(new AgentRequestScope());
  const editorAgentId = useRef<string | null>(null);

  // Data & View State
  const [agents, setAgents] = useState<AgentDraftMetadata[]>([]);
  const [viewState, setViewState] = useState<ViewState>("loading");
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [errorStatus, setErrorStatus] = useState<number | undefined>(undefined);
  const [requestId, setRequestId] = useState<string | undefined>(undefined);

  // View Mode: Table vs Grid Cards
  const [viewMode, setViewMode] = useState<ViewMode>("table");

  // Search & Filter
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const itemsPerPage = 8;

  // Create Modal State
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<StarterTemplate | null>(null);
  const [createError, setCreateError] = useState("");
  const [isSubmittingCreate, setIsSubmittingCreate] = useState(false);

  // Editor Drawer / Workshop State
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [activeDraft, setActiveDraft] = useState<AgentDraft | null>(null);

  // Publish Dialog State (04-C, R04C-04, R04C-05)
  const [publishTargetAgent, setPublishTargetAgent] = useState<AgentDraftMetadata | null>(null);
  const [publishIdempotencyKey, setPublishIdempotencyKey] = useState<string>("");
  const [publishRevision, setPublishRevision] = useState<number>(1);
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishReceipt, setPublishReceipt] = useState<PublishResponse | null>(null);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);
  const [isRecoveredIntent, setIsRecoveredIntent] = useState(false);
  const [pendingCreatedAt, setPendingCreatedAt] = useState<number | null>(null);

  // Version History & Promotion State (04-D)
  const [historyVersions, setHistoryVersions] = useState<AgentVersionMetadata[]>([]);
  const [historyPublications, setHistoryPublications] = useState<AgentPublication[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyAgentId, setHistoryAgentId] = useState<string | null>(null);
  const [historyNextCursor, setHistoryNextCursor] = useState<string | null>(null);
  const [isLoadingHistoryMore, setIsLoadingHistoryMore] = useState(false);

  // Promote / Rollback Modal State
  const [isPromoteModalOpen, setIsPromoteModalOpen] = useState(false);
  const [promoteTargetVersion, setPromoteTargetVersion] = useState<AgentVersionMetadata | null>(null);
  const [promoteEnvironment, setPromoteEnvironment] = useState<AgentEnvironment>("development");
  const [isPromoting, setIsPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  const [promoteConflict, setPromoteConflict] = useState(false);

  // UI Toast & Clipboard Feedback
  const [toast, setToast] = useState<ToastNotification | null>(null);
  const toastTimerRef = useRef<NodeJS.Timeout | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Focus and trigger references for accessibility
  const searchFieldRef = useRef<HTMLInputElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);

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

  const handleCopyText = useCallback((text: string, label: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedKey(label);
      setTimeout(() => setCopiedKey(null), 2500);
      showToast(`Đã sao chép ${label} vào clipboard.`, "info");
    }
  }, [showToast]);

  // Reset all scoped state and abort in-flight requests on workspace or actor switch (RF04-08)
  useLayoutEffect(() => {
    const scope = requestScope.current;
    const list = listScope.current;
    const editor = editorScope.current;
    const publish = publishScope.current;
    const promote = promoteScope.current;
    const history = historyScope.current;
    const promoteDialog = promoteDialogScope.current;

    scope.reset();
    list.reset();
    editor.invalidate();
    editorAgentId.current = null;
    publish.invalidate();
    promote.invalidate();
    history.invalidate();
    promoteDialog.invalidate();

    setAgents([]);
    setViewState("loading");
    setIsRefreshing(false);
    setErrorMessage("");
    setErrorStatus(undefined);
    setRequestId(undefined);
    setSearchQuery("");
    setStatusFilter("ALL");
    setCurrentPage(1);
    setNextCursor(null);
    setIsLoadingMore(false);

    setIsCreateModalOpen(false);
    setSelectedTemplate(null);
    setCreateError("");
    setIsSubmittingCreate(false);

    setHistoryVersions([]);
    setHistoryPublications([]);
    setHistoryNextCursor(null);
    setHistoryAgentId(null);
    setHistoryError(null);
    setIsLoadingHistory(false);
    setIsLoadingHistoryMore(false);
    setIsPromoteModalOpen(false);
    setPromoteTargetVersion(null);
    setIsPromoting(false);
    setPromoteError(null);
    setPromoteConflict(false);
    setActiveDraft(null);
    setIsEditorOpen(false);
    setPublishTargetAgent(null);
    setPublishIdempotencyKey("");
    setPublishRevision(1);
    setIsPublishing(false);
    setPublishReceipt(null);
    setPublishMessage(null);
    setIsRecoveredIntent(false);
    setPendingCreatedAt(null);
    dismissToast();

    return () => {
      scope.invalidate();
      list.invalidate();
      editor.invalidate();
      publish.invalidate();
      promote.invalidate();
      history.invalidate();
      promoteDialog.invalidate();
    };
  }, [workspaceId, actorId, dismissToast]);

  // Fetch agents from API (RF04-08: bound to both workspaceId and actorId)
  const fetchAgents = useCallback(async () => {
    if (!workspaceId || !actorId) return;
    listScope.current.reset();
    const request = listScope.current.capture();
    const workspace = requestScope.current.capture();

    setIsRefreshing(true);
    setViewState("loading");
    setErrorMessage("");
    setErrorStatus(undefined);
    setRequestId(undefined);

    try {
      const page = await defaultApiClient.listAgents(workspaceId, {
        signal: request.signal,
      });
      if (!workspace.isCurrent() || !request.isCurrent()) return;

      const items = page.items || [];
      setAgents(items);
      setNextCursor(page.next_cursor);
      setCurrentPage(1);
      setViewState(items.length === 0 ? "empty" : "ready");
    } catch (err: unknown) {
      if (!workspace.isCurrent() || !request.isCurrent()) return;

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
          typeof err.body === "object" && err.body !== null && "detail" in err.body
            ? (err.body as { detail: { message?: string } }).detail?.message || err.message
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
      if (workspace.isCurrent() && request.isCurrent()) {
        setIsRefreshing(false);
      }
    }
  }, [workspaceId, actorId, router]);

  // Initial load on workspace or actor change (RF04-08)
  useEffect(() => {
    if (!workspaceId || !actorId) return;
    fetchAgents();

    return () => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, [fetchAgents, workspaceId, actorId]);

  // Load more agents (Cursor pagination)
  const handleLoadMoreAgents = async () => {
    if (!nextCursor || isLoadingMore) return;
    const request = listScope.current.capture();
    const workspace = requestScope.current.capture();
    setIsLoadingMore(true);

    try {
      const page = await defaultApiClient.listAgents(workspaceId, {
        cursor: nextCursor,
        signal: request.signal,
      });
      if (!workspace.isCurrent() || !request.isCurrent()) return;

      setAgents((previous) => {
        const known = new Set(previous.map((a) => a.agent_id));
        return [...previous, ...page.items.filter((a) => !known.has(a.agent_id))];
      });
      setNextCursor(page.next_cursor);
    } catch {
      if (workspace.isCurrent() && request.isCurrent()) {
        showToast("Không thể tải thêm tác tử. Hãy thử lại.", "error");
      }
    } finally {
      if (workspace.isCurrent() && request.isCurrent()) {
        setIsLoadingMore(false);
      }
    }
  };

  // Filtered & Paginated items (P1-02: reset and clamp currentPage)
  const filteredAgents = useMemo(() => {
    return agents.filter((item) => {
      const query = searchQuery.trim().toLowerCase();
      const matchQuery =
        !query ||
        item.display_name.toLowerCase().includes(query) ||
        item.slug.toLowerCase().includes(query);

      const matchStatus =
        statusFilter === "ALL" ||
        (statusFilter === "DRAFT" && !item.archived_at) ||
        (statusFilter === "ARCHIVED" && Boolean(item.archived_at));

      return matchQuery && matchStatus;
    });
  }, [agents, searchQuery, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredAgents.length / itemsPerPage));

  // Reset page to 1 when search or status filter changes
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, statusFilter]);

  // Clamp current page when filtered elements shrink
  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  const handleResetFilters = () => {
    setSearchQuery("");
    setStatusFilter("ALL");
  };

  // Create Draft Action (P1-03)
  const handleOpenCreateModal = useCallback(() => {
    setSelectedTemplate(null);
    setCreateError("");
    setIsCreateModalOpen(true);
  }, []);

  const handleApplyTemplate = useCallback((template: StarterTemplate) => {
    setSelectedTemplate(template);
    setCreateError("");
    setIsCreateModalOpen(true);
    showToast(
      `Đã áp dụng mẫu "${template.name}". Hãy kiểm tra và nhấn Tạo bản nháp.`,
      "info"
    );
  }, [showToast]);

  const handleSubmitCreateAgent = async (dto: CreateAgentDraftDTO): Promise<boolean> => {
    setIsSubmittingCreate(true);
    setCreateError("");

    const workspace = requestScope.current.capture();
    try {
      const created = await defaultApiClient.createAgentDraft(
        workspaceId,
        dto,
        workspace.signal
      );
      if (!workspace.isCurrent()) return false;

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
      showToast(
        `Đã tạo bản nháp Tác tử "${meta.display_name}" thành công (revision ${meta.revision}).`,
        "success"
      );
      if (viewState === "empty") setViewState("ready");
      return true;
    } catch (err: unknown) {
      if (!workspace.isCurrent()) return false;

      if (err instanceof ApiClientError) {
        if (err.isUnauthorized) {
          router.replace(
            `/login?error=session_expired&return_to=${encodeURIComponent(
              window.location.pathname
            )}`
          );
          return false;
        }
        if (err.status === 409) {
          setCreateError(`Định danh slug "${dto.slug}" đã tồn tại trong Workspace này.`);
          return false;
        }
        if (err.status === 403) {
          setCreateError(
            "Từ chối truy cập: Bạn không có quyền khởi tạo Tác tử (cần vai trò Developer, Admin, hoặc Owner)."
          );
          return false;
        }
        if (err.status === 422) {
          const detail =
            typeof err.body === "object" && err.body !== null && "detail" in err.body
              ? (err.body as { detail: { message?: string } }).detail?.message
              : undefined;
          setCreateError(`Dữ liệu không hợp lệ: ${detail || "AgentSpec không đúng định dạng chuẩn."}`);
          return false;
        }
        setCreateError(err.message || "Lỗi máy chủ khi tạo bản nháp Agent.");
      } else {
        setCreateError(err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định.");
      }
      return false;
    } finally {
      if (workspace.isCurrent()) {
        setIsSubmittingCreate(false);
      }
    }
  };

  // Open Editor
  const handleOpenEditor = async (agent: AgentDraftMetadata) => {
    editorScope.current.reset();
    historyScope.current.invalidate();
    promoteScope.current.invalidate();
    promoteDialogScope.current.invalidate();
    editorAgentId.current = agent.agent_id;

    setActiveDraft(null);
    setHistoryVersions([]);
    setHistoryPublications([]);
    setHistoryAgentId(null);
    setHistoryNextCursor(null);
    setHistoryError(null);
    setIsLoadingHistoryMore(false);
    setIsPromoting(false);
    setIsPromoteModalOpen(false);
    setPromoteTargetVersion(null);

    const workspace = requestScope.current.capture();
    const editor = editorScope.current.capture();

    try {
      const draft = await defaultApiClient.getAgentDraft(
        workspaceId,
        agent.agent_id,
        editor.signal
      );
      if (!workspace.isCurrent() || !editor.isCurrent()) return;

      setActiveDraft(draft);
      setIsEditorOpen(true);
      fetchAgentHistory(agent.agent_id);
    } catch (err: unknown) {
      if (!workspace.isCurrent() || !editor.isCurrent()) return;
      if (err instanceof ApiClientError && err.status === 404) {
        showToast("Tác tử không tồn tại trong Workspace này.", "error");
        fetchAgents();
        return;
      }
      showToast("Không thể tải chi tiết bản nháp Agent.", "error");
    }
  };

  const handleCloseEditor = () => {
    editorScope.current.invalidate();
    historyScope.current.invalidate();
    promoteScope.current.invalidate();
    promoteDialogScope.current.invalidate();
    editorAgentId.current = null;
    setIsEditorOpen(false);
    setActiveDraft(null);
    setHistoryVersions([]);
    setHistoryPublications([]);
    setHistoryAgentId(null);
    setHistoryNextCursor(null);
    setHistoryError(null);
    setIsLoadingHistory(false);
    setIsLoadingHistoryMore(false);
    setIsPromoting(false);
    setIsPromoteModalOpen(false);
    setPromoteTargetVersion(null);
  };

  const handleDraftSaved = (updatedDraft: AgentDraft) => {
    setActiveDraft(updatedDraft);
    setAgents((prev) =>
      prev.map((ag) =>
        ag.agent_id === updatedDraft.agent_id
          ? { ...ag, revision: updatedDraft.revision }
          : ag
      )
    );
  };

  // ========================================================
  // PUBLISH STATE & DIALOG (04-C, R04C-04, R04C-05, P0-03)
  // ========================================================
  const createPublishKey = (): string | null => {
    try {
      return generateSecureIdempotencyKey();
    } catch {
      showToast("Trình duyệt không hỗ trợ tạo khóa an toàn. Không gửi yêu cầu xuất bản.", "error");
      return null;
    }
  };

  const handleOpenPublishModal = (agent: AgentDraftMetadata) => {
    const pending = getPendingPublishIntent(actorId, workspaceId, agent.agent_id);
    const recovered = pending && (pending.status === "pending" || pending.status === "unknown");
    const key = recovered ? pending.operationKey : createPublishKey();
    if (!key) return;
    setPublishMessage(null);
    setPublishReceipt(null);
    setPublishTargetAgent(agent);

    if (recovered) {
      setPublishIdempotencyKey(pending.operationKey);
      setPublishRevision(pending.expectedRevision);
      setPendingCreatedAt(pending.createdAt || Date.now());
      setIsRecoveredIntent(true);
      setPublishMessage(null);
    } else {
      setPublishIdempotencyKey(key);
      setPublishRevision(agent.revision);
      setPendingCreatedAt(null);
      setIsRecoveredIntent(false);
    }
  };

  const handleClosePublishModal = () => {
    publishScope.current.invalidate();
    setPublishTargetAgent(null);
    setPublishMessage(null);
    setIsPublishing(false);
    setPublishReceipt(null);
    setIsRecoveredIntent(false);
    setPendingCreatedAt(null);
  };

  const handleCreateFreshIntent = () => {
    if (!publishTargetAgent) return;
    const freshKey = createPublishKey();
    if (!freshKey) return;
    clearPendingPublishIntent(actorId, workspaceId, publishTargetAgent.agent_id);
    setPublishIdempotencyKey(freshKey);
    setPublishRevision(publishTargetAgent.revision);
    setPendingCreatedAt(null);
    setIsRecoveredIntent(false);
    setPublishMessage(null);
    showToast("Đã hủy ý định cũ và tạo khóa xuất bản mới.", "info");
  };

  const handleConfirmPublish = async () => {
    if (!publishTargetAgent) return;

    const targetActorId = actorId;
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
      actorId: targetActorId,
      workspaceId,
      agentId: targetAgentId,
      operationKey: targetKey,
      expectedRevision: targetRevision,
      status: "pending",
      createdAt: Date.now(),
    });

    setIsPublishing(true);
    setPublishMessage(null);
    setPublishReceipt(null);

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

      if (!workspaceSignal.isCurrent() || !publishOp.isCurrent()) return;
      if (!publishOp.matchesResponse(res)) return;

      // Confirmed success: clear pending intent
      clearPendingPublishIntent(targetActorId, workspaceId, targetAgentId);

      setPublishReceipt(res);
      setIsPublishing(false);
      showToast(
        `Đã xuất bản Tác tử "${targetAgent.display_name}" thành phiên bản ${res.version}.`,
        "success"
      );
      await fetchAgents();
    } catch (err: unknown) {
      if (!workspaceSignal.isCurrent() || !publishOp.isCurrent()) return;

      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          clearPendingPublishIntent(targetActorId, workspaceId, targetAgentId);
          setPublishMessage(
            "Bản nháp đã bị thay đổi trước khi xuất bản. Vui lòng đóng hộp thoại và tải lại trang."
          );
          return;
        }
        if (err.status === 422) {
          clearPendingPublishIntent(targetActorId, workspaceId, targetAgentId);
          setPublishMessage(
            "Tác tử chưa sẵn sàng xuất bản: phụ thuộc hoặc cấu hình không hợp lệ. Vui lòng kiểm tra lại chẩn đoán."
          );
          return;
        }
        if (err.status === 403) {
          clearPendingPublishIntent(targetActorId, workspaceId, targetAgentId);
          setPublishMessage("Từ chối truy cập: Bạn không có quyền xuất bản Tác tử.");
          return;
        }
        if (err.status === 409) {
          setPublishMessage(
            "Xung đột xuất bản: Khóa idempotency đã được dùng với yêu cầu khác hoặc đang được xử lý đồng thời."
          );
          return;
        }
        // Other API errors: keep intent in storage as unknown for recovery
        savePendingPublishIntent({
          actorId: targetActorId,
          workspaceId,
          agentId: targetAgentId,
          operationKey: targetKey,
          expectedRevision: targetRevision,
          status: "unknown",
          createdAt: Date.now(),
        });
        setPublishMessage(err.message || "Dịch vụ xuất bản tạm thời không khả dụng.");
      } else {
        // Network timeout / lost connection: PRESERVE intent (P0-03)
        savePendingPublishIntent({
          actorId: targetActorId,
          workspaceId,
          agentId: targetAgentId,
          operationKey: targetKey,
          expectedRevision: targetRevision,
          status: "unknown",
          createdAt: Date.now(),
        });
        setPublishMessage(
          "Không thể xác nhận phản hồi từ máy chủ. Yêu cầu xuất bản đã được lưu để bạn có thể tiếp tục kiểm tra và khôi phục (Verify & Recover)."
        );
      }
    } finally {
      if (workspaceSignal.isCurrent() && publishOp.isCurrent()) {
        setIsPublishing(false);
      }
    }
  };

  // ========================================================
  // VERSION HISTORY & PROMOTION HANDLERS (04-D, P1-01)
  // Required by agent-lifecycle-handlers.test.ts
  // ========================================================
  const fetchAgentHistory = useCallback(
    async (agentId: string, cursor?: string): Promise<boolean> => {
      if (editorAgentId.current !== agentId) return false;
      historyScope.current.reset();
      const request = historyScope.current.capture();
      const editor = editorScope.current.capture();
      const scope = requestScope.current.capture();
      const isCurrent = () =>
        scope.isCurrent() &&
        editor.isCurrent() &&
        request.isCurrent() &&
        editorAgentId.current === agentId;

      if (cursor) {
        setIsLoadingHistoryMore(true);
      } else {
        setIsLoadingHistory(true);
        setIsLoadingHistoryMore(false);
        setHistoryAgentId(null);
        setHistoryVersions([]);
        setHistoryPublications([]);
        setHistoryNextCursor(null);
      }
      setHistoryError(null);

      try {
        const [versionsRes, publicationsRes] = await Promise.all([
          defaultApiClient.listAgentVersions(workspaceId, agentId, {
            cursor,
            signal: request.signal,
          }),
          defaultApiClient.listAgentPublications(workspaceId, agentId, request.signal),
        ]);
        if (!isCurrent()) return false;
        if (
          versionsRes.items.some((item) => item.agent_id !== agentId) ||
          publicationsRes.items.some((item) => item.agent_id !== agentId)
        ) {
          throw new Error("History response does not match the selected Agent");
        }

        setHistoryVersions((previous) => {
          const versions = cursor ? [...previous, ...versionsRes.items] : versionsRes.items;
          return Array.from(new Map(versions.map((item) => [item.agent_version_id, item])).values());
        });
        setHistoryPublications(publicationsRes.items);
        setHistoryNextCursor(versionsRes.next_cursor);
        setHistoryAgentId(agentId);
        return true;
      } catch (err: unknown) {
        if (!isCurrent()) return false;
        if (err instanceof ApiClientError) {
          setHistoryError(err.message || "Không thể tải lịch sử phiên bản của tác tử.");
        } else {
          setHistoryError("Đã xảy ra lỗi không xác định khi tải lịch sử phiên bản.");
        }
        return false;
      } finally {
        if (isCurrent()) {
          setIsLoadingHistory(false);
          setIsLoadingHistoryMore(false);
        }
      }
    },
    [workspaceId]
  );

  const handleOpenPromoteModal = (version: AgentVersionMetadata) => {
    if (
      !activeDraft ||
      version.agent_id !== activeDraft.agent_id ||
      historyAgentId !== activeDraft.agent_id
    )
      return;
    promoteScope.current.invalidate();
    promoteDialogScope.current.reset();
    setIsPromoting(false);
    setPromoteTargetVersion(version);
    setPromoteEnvironment("development");
    setPromoteError(null);
    setPromoteConflict(false);
    setIsPromoteModalOpen(true);
    void fetchAgentHistory(activeDraft.agent_id);
  };

  const handleClosePromoteModal = () => {
    promoteScope.current.invalidate();
    promoteDialogScope.current.invalidate();
    setIsPromoting(false);
    setIsPromoteModalOpen(false);
    setPromoteTargetVersion(null);
    setPromoteError(null);
    setPromoteConflict(false);
    if (isPromoting) {
      showToast(
        "Kết quả điều chuyển chưa được xác nhận. Khi mở lại, hệ thống sẽ tải trạng thái môi trường từ máy chủ.",
        "info"
      );
    }
  };

  const handleReloadPromotionState = async () => {
    if (!activeDraft) return;
    const dialog = promoteDialogScope.current.capture();
    const editor = editorScope.current.capture();
    const scope = requestScope.current.capture();
    const loaded = await fetchAgentHistory(activeDraft.agent_id);
    if (loaded && scope.isCurrent() && editor.isCurrent() && dialog.isCurrent()) {
      setPromoteError(null);
      setPromoteConflict(false);
    }
  };

  const handleConfirmPromote = async () => {
    if (
      !promoteTargetVersion ||
      !activeDraft ||
      isPromoting ||
      promoteConflict ||
      isLoadingHistory ||
      isLoadingHistoryMore ||
      historyError ||
      historyAgentId !== activeDraft.agent_id ||
      promoteTargetVersion.agent_id !== activeDraft.agent_id
    )
      return;

    const targetVersion = promoteTargetVersion;
    const agentId = activeDraft.agent_id;
    const environment = promoteEnvironment;

    const currentPub = historyPublications.find((p) => p.environment === environment);
    const expectedRevision = currentPub ? currentPub.revision : 0;

    const promoOp = promoteScope.current.begin(
      workspaceId,
      agentId,
      environment,
      expectedRevision
    );

    setIsPromoting(true);
    setPromoteError(null);
    setPromoteConflict(false);

    const workspaceSignal = requestScope.current.capture();
    const editor = editorScope.current.capture();

    try {
      const res = await defaultApiClient.promoteAgentVersion(
        workspaceId,
        agentId,
        {
          agent_version_id: targetVersion.agent_version_id,
          environment,
          expected_publication_revision: expectedRevision,
        },
        promoOp.signal
      );

      if (!workspaceSignal.isCurrent() || !editor.isCurrent() || !promoOp.isCurrent()) return;
      if (!promoOp.matchesResponse(res)) return;

      setIsPromoting(false);
      setIsPromoteModalOpen(false);

      if (res.is_noop) {
        showToast(
          `Phiên bản v${res.version} đã được điều chuyển sẵn tới môi trường ${environment} (No-op).`,
          "info"
        );
      } else if (currentPub && res.version < currentPub.version) {
        showToast(
          `Đã quay lui môi trường ${environment} về phiên bản v${res.version}.`,
          "success"
        );
      } else {
        showToast(
          `Đã điều chuyển phiên bản v${res.version} lên môi trường ${environment}.`,
          "success"
        );
      }

      await fetchAgentHistory(agentId);
      await fetchAgents();
    } catch (err: unknown) {
      if (!workspaceSignal.isCurrent() || !editor.isCurrent() || !promoOp.isCurrent()) return;
      setIsPromoting(false);

      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          setPromoteConflict(true);
          setPromoteError(
            "Xung đột phiên bản: Môi trường này vừa được cập nhật bởi một phiên làm việc khác (Revision mismatch). Vui lòng nhấn 'Tải lại' để xem con trỏ mới nhất."
          );
          return;
        }
        if (err.status === 403) {
          setPromoteError(
            "Từ chối truy cập: Chỉ Owner hoặc Admin của Không gian làm việc mới có quyền điều chuyển phiên bản (agent:promote)."
          );
          return;
        }
        if (err.status === 422) {
          setPromoteError(
            `Không thể điều chuyển: ${err.message || "Phiên bản chưa được niêm phong hoặc phụ thuộc không hợp lệ."}`
          );
          return;
        }
        setPromoteConflict(true);
        setPromoteError(
          "Chưa xác nhận được kết quả điều chuyển. Hãy tải lại trạng thái môi trường trước khi xác nhận một thao tác mới."
        );
      } else {
        setPromoteConflict(true);
        setPromoteError(
          "Chưa xác nhận được kết quả điều chuyển. Hãy tải lại trạng thái môi trường trước khi xác nhận một thao tác mới."
        );
      }
    } finally {
      if (workspaceSignal.isCurrent() && editor.isCurrent() && promoOp.isCurrent()) {
        setIsPromoting(false);
      }
    }
  };

  const handleSetPromoteEnvironment = (environment: AgentEnvironment) => {
    setPromoteEnvironment(environment);
    // An unresolved command stays unresolved until authoritative reconciliation.
    if (!promoteConflict) setPromoteError(null);
  };

  // Keyboard shortcut listener (Accessibility)
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement as HTMLElement | null;
      const isInputFocused =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          activeEl.tagName === "SELECT" ||
          activeEl.isContentEditable);

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchFieldRef.current?.focus();
        searchFieldRef.current?.select();
        return;
      }

      if (
        e.key === "/" &&
        !isInputFocused &&
        !isCreateModalOpen &&
        !isEditorOpen &&
        !publishTargetAgent &&
        !isPromoteModalOpen
      ) {
        e.preventDefault();
        searchFieldRef.current?.focus();
        searchFieldRef.current?.select();
        return;
      }

      if (
        !isCreateModalOpen &&
        !isEditorOpen &&
        !publishTargetAgent &&
        !isPromoteModalOpen &&
        (((e.key === "c" || e.key === "C") &&
          !e.ctrlKey &&
          !e.metaKey &&
          !e.altKey &&
          !isInputFocused) ||
          (e.altKey && e.key.toLowerCase() === "n"))
      ) {
        e.preventDefault();
        handleOpenCreateModal();
      }
    };

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [
    isCreateModalOpen,
    isEditorOpen,
    publishTargetAgent,
    isPromoteModalOpen,
    handleOpenCreateModal,
  ]);

  return (
    <div className={styles.agentsPageRoot}>
      {/* Toast Notification */}
      {toast && (
        <div
          role={toast.type === "error" ? "alert" : "status"}
          aria-live={toast.type === "error" ? "assertive" : "polite"}
          aria-atomic="true"
          className={`agent-toast-card toast-${toast.type}`}
        >
          <div className="toast-content-wrapper">
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

      {/* Agent List Component */}
      <AgentList
        agents={agents}
        filteredAgents={filteredAgents}
        viewState={viewState}
        viewMode={viewMode}
        onSetViewMode={setViewMode}
        searchQuery={searchQuery}
        onSetSearchQuery={setSearchQuery}
        statusFilter={statusFilter}
        onSetStatusFilter={setStatusFilter}
        onResetFilters={handleResetFilters}
        onRefresh={fetchAgents}
        isRefreshing={isRefreshing}
        currentPage={currentPage}
        onPageChange={setCurrentPage}
        totalPages={totalPages}
        itemsPerPage={itemsPerPage}
        nextCursor={nextCursor}
        isLoadingMore={isLoadingMore}
        onLoadMore={handleLoadMoreAgents}
        onOpenEditor={handleOpenEditor}
        onOpenPublish={handleOpenPublishModal}
        onOpenCreate={handleOpenCreateModal}
        onApplyTemplate={handleApplyTemplate}
        searchFieldRef={searchFieldRef}
        errorMessage={errorMessage}
        errorStatus={errorStatus}
        requestId={requestId}
        onContactAdmin={() =>
          showToast(
            "Vui lòng liên hệ với Quản trị viên (Admin hoặc Owner) của Workspace để được cấp vai trò 'agent:read'.",
            "info"
          )
        }
      />

      {/* Create Agent Modal */}
      <AgentCreateModal
        key={`create-${workspaceId}:${actorId}`}
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onSubmit={handleSubmitCreateAgent}
        isSubmitting={isSubmittingCreate}
        errorMessage={createError}
        onClearError={() => setCreateError("")}
        initialTemplate={selectedTemplate}
      />

      {/* Agent Studio Workshop Modal */}
      <AgentWorkshop
        key={`workshop-${workspaceId}:${actorId}`}
        isOpen={isEditorOpen}
        onClose={handleCloseEditor}
        activeDraft={activeDraft}
        workspaceId={workspaceId}
        onDraftSaved={handleDraftSaved}
        historyVersions={historyVersions}
        historyPublications={historyPublications}
        isLoadingHistory={isLoadingHistory}
        isLoadingHistoryMore={isLoadingHistoryMore}
        historyError={historyError}
        historyNextCursor={historyNextCursor}
        onRefreshHistory={() => activeDraft && fetchAgentHistory(activeDraft.agent_id)}
        onLoadMoreHistory={() =>
          activeDraft && fetchAgentHistory(activeDraft.agent_id, historyNextCursor || undefined)
        }
        onOpenPromoteModal={handleOpenPromoteModal}
        onShowToast={showToast}
        onCopyText={handleCopyText}
        copiedKey={copiedKey}
      />

      {/* Publish Release Modal */}
      <AgentPublishModal
        targetAgent={publishTargetAgent}
        isOpen={Boolean(publishTargetAgent)}
        onClose={handleClosePublishModal}
        idempotencyKey={publishIdempotencyKey}
        expectedRevision={publishRevision}
        isPublishing={isPublishing}
        publishReceipt={publishReceipt}
        publishMessage={publishMessage}
        isRecoveredIntent={isRecoveredIntent}
        pendingCreatedAt={pendingCreatedAt}
        onConfirmPublish={handleConfirmPublish}
        onCreateFreshIntent={handleCreateFreshIntent}
        onCopyText={handleCopyText}
        copiedKey={copiedKey}
        onGoToOverview={() => {
          handleClosePublishModal();
          router.push(`/w/${workspaceId}/overview`);
        }}
      />

      {/* Promote / Rollback Modal */}
      <AgentPromoteModal
        isOpen={isPromoteModalOpen && Boolean(promoteTargetVersion)}
        onClose={handleClosePromoteModal}
        targetVersion={promoteTargetVersion}
        agentDisplayName={activeDraft?.display_name || ""}
        publications={historyPublications}
        promoteEnvironment={promoteEnvironment}
        onSetEnvironment={handleSetPromoteEnvironment}
        isPromoting={isPromoting}
        promoteError={promoteError}
        promoteConflict={promoteConflict}
        onConfirmPromote={handleConfirmPromote}
        onReloadPromotionState={handleReloadPromotionState}
        isLoadingHistory={isLoadingHistory}
        isLoadingHistoryMore={isLoadingHistoryMore}
        historyError={historyError}
      />
    </div>
  );
}
