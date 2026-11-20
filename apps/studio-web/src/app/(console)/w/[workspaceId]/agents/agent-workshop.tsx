"use client";

import React, { useState, useLayoutEffect, useMemo, useRef, useCallback } from "react";
import { Modal, Button } from "@enterprise/ui";
import {
  AgentDraft,
  AgentValidationResult,
  AgentVersionMetadata,
  AgentPublication,
  defaultApiClient,
  ApiClientError,
} from "@enterprise/api-client";
import {
  AgentDraftEdits,
  AgentRequestScope,
  AgentValidationScope,
  estimateTokenCount,
  detectPromptSafetyRisk,
  categorizeDiagnostics,
  CategorizedDiagnostic,
  isValidationResultStale,
  calculateThreeWayDraftDiff,
  buildThreeWayMergedSpec,
  extractEditsFromSpec,
  ThreeWayDiffResult,
  mergeAgentDraftEdits,
} from "../../../../../lib/agent-draft-state";
import { ExecutionMode } from "./agent-list";

export type WorkshopTab = "persona" | "model" | "dependencies" | "history";

export interface AgentWorkshopProps {
  isOpen: boolean;
  onClose: () => void;
  activeDraft: AgentDraft | null;
  workspaceId: string;
  onDraftSaved: (updatedDraft: AgentDraft) => void;
  // History & Promotion props
  historyVersions: AgentVersionMetadata[];
  historyPublications: AgentPublication[];
  isLoadingHistory: boolean;
  isLoadingHistoryMore: boolean;
  historyError: string | null;
  historyNextCursor: string | null;
  onRefreshHistory: () => void;
  onLoadMoreHistory: () => void;
  onOpenPromoteModal: (version: AgentVersionMetadata) => void;
  onShowToast: (message: string, type?: "success" | "error" | "info") => void;
  onCopyText: (text: string, label: string) => void;
  copiedKey: string | null;
}

export function AgentWorkshop({
  isOpen,
  onClose,
  activeDraft,
  workspaceId,
  onDraftSaved,
  historyVersions,
  historyPublications,
  isLoadingHistory,
  isLoadingHistoryMore,
  historyError,
  historyNextCursor,
  onRefreshHistory,
  onLoadMoreHistory,
  onOpenPromoteModal,
  onShowToast,
  onCopyText,
  copiedKey,
}: AgentWorkshopProps) {
  const [activeTab, setActiveTab] = useState<WorkshopTab>("persona");

  // Base snapshot at load/save time for 3-way conflict resolution
  const [baseSpec, setBaseSpec] = useState<Record<string, unknown> | null>(null);
  const baseSpecRef = useRef<Record<string, unknown> | null>(null);

  // Form field states
  const [instructions, setInstructions] = useState("");
  const [modelRef, setModelRef] = useState("");
  const [mode, setMode] = useState<ExecutionMode>("fixed_plan");
  const [maxSteps, setMaxSteps] = useState(10);
  const [maxWallTime, setMaxWallTime] = useState(180);
  const [maxTokens, setMaxTokens] = useState(16000);
  const editGenerationRef = useRef(0);
  const hydratedScopeRef = useRef<string | null>(null);
  const latestEditsRef = useRef<AgentDraftEdits>({
    mode: "fixed_plan",
    modelRef: "",
    instructions: "",
    maxSteps: 10,
    maxWallTimeSeconds: 180,
    maxTotalTokens: 16000,
  });

  // Validation states
  const validationScope = useRef(new AgentValidationScope());
  const editorLifetime = useRef(new AgentRequestScope());
  const saveScope = useRef(new AgentRequestScope());
  const [isValidating, setIsValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<AgentValidationResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [hasEditsSinceValidation, setHasEditsSinceValidation] = useState(false);

  // Save & 412 Conflict states
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  interface ConflictWorkbenchState {
    serverDraft: AgentDraft;
    diffResult: ThreeWayDiffResult;
    userResolutions: Record<keyof AgentDraftEdits, "local" | "server">;
  }
  const [conflictWorkbench, setConflictWorkbench] = useState<ConflictWorkbenchState | null>(null);
  const conflictWorkbenchRef = useRef<ConflictWorkbenchState | null>(null);
  const isFormLocked = isSaving || Boolean(conflictWorkbench);

  // Line numbers gutter ref
  const gutterRef = useRef<HTMLDivElement>(null);
  const handleTextareaScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
    if (gutterRef.current) {
      gutterRef.current.scrollTop = e.currentTarget.scrollTop;
    }
  };

  // Centralized Validation Invalidation (P0-02)
  const invalidateValidation = useCallback(() => {
    validationScope.current.invalidate();
    setIsValidating(false);
    setValidationError(null);
    setHasEditsSinceValidation(true);
  }, []);

  const activeAgentId = activeDraft?.agent_id;

  useLayoutEffect(() => {
    const editor = editorLifetime.current;
    const saving = saveScope.current;
    const validation = validationScope.current;
    if (isOpen && activeAgentId) editor.reset();
    else editor.invalidate();
    saving.invalidate();
    validation.invalidate();
    setIsSaving(false);
    setIsValidating(false);
    return () => {
      editor.invalidate();
      saving.invalidate();
      validation.invalidate();
    };
  }, [workspaceId, activeAgentId, isOpen]);

  // Replace form state before painting a different Agent's editor (RF04-01: do not clobber edits on same-agent revision refresh).
  useLayoutEffect(() => {
    if (!isOpen || !activeDraft) {
      hydratedScopeRef.current = null;
      conflictWorkbenchRef.current = null;
      setConflictWorkbench(null);
      return;
    }

    const nextScopeKey = `${workspaceId}:${activeDraft.agent_id}`;
    if (hydratedScopeRef.current === nextScopeKey) {
      return;
    }

    const spec = activeDraft.spec || {};
    const clonedSpec = JSON.parse(JSON.stringify(spec)) as Record<string, unknown>;
    const edits = extractEditsFromSpec(spec);
    hydratedScopeRef.current = nextScopeKey;
    editGenerationRef.current = 0;
    baseSpecRef.current = clonedSpec;
    latestEditsRef.current = { ...edits };
    conflictWorkbenchRef.current = null;

    setBaseSpec(clonedSpec);
    setInstructions(edits.instructions);
    setModelRef(edits.modelRef);
    setMode(edits.mode as ExecutionMode);
    setMaxSteps(edits.maxSteps);
    setMaxWallTime(edits.maxWallTimeSeconds);
    setMaxTokens(edits.maxTotalTokens);

    validationScope.current.invalidate();
    setValidationResult(null);
    setValidationError(null);
    setHasEditsSinceValidation(false);
    setSaveError(null);
    setConflictWorkbench(null);
    setActiveTab("persona");
  }, [workspaceId, activeDraft, isOpen]);

  // Centralized field updaters (P0-02 + RF04-01: track edit generation & block drift while conflict workbench is active)
  const updateInstructions = useCallback((val: string) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, instructions: val };
    setInstructions(val);
    invalidateValidation();
  }, [invalidateValidation]);

  const updateModelRef = useCallback((val: string) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, modelRef: val };
    setModelRef(val);
    invalidateValidation();
  }, [invalidateValidation]);

  const updateMode = useCallback((val: ExecutionMode) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, mode: val };
    setMode(val);
    invalidateValidation();
  }, [invalidateValidation]);

  const updateMaxSteps = useCallback((val: number) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, maxSteps: val };
    setMaxSteps(val);
    invalidateValidation();
  }, [invalidateValidation]);

  const updateMaxWallTime = useCallback((val: number) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, maxWallTimeSeconds: val };
    setMaxWallTime(val);
    invalidateValidation();
  }, [invalidateValidation]);

  const updateMaxTokens = useCallback((val: number) => {
    if (conflictWorkbenchRef.current !== null) return;
    editGenerationRef.current += 1;
    latestEditsRef.current = { ...latestEditsRef.current, maxTotalTokens: val };
    setMaxTokens(val);
    invalidateValidation();
  }, [invalidateValidation]);

  // Dirty check
  const isDirty = useMemo(() => {
    if (!baseSpec) return false;
    const base = extractEditsFromSpec(baseSpec);
    return (
      instructions.trim() !== base.instructions.trim() ||
      modelRef.trim() !== base.modelRef.trim() ||
      mode !== base.mode ||
      maxSteps !== base.maxSteps ||
      maxWallTime !== base.maxWallTimeSeconds ||
      maxTokens !== base.maxTotalTokens
    );
  }, [baseSpec, instructions, modelRef, mode, maxSteps, maxWallTime, maxTokens]);

  const handleSafeClose = useCallback(() => {
    if (isSaving || isValidating) return;
    if (isDirty) {
      const confirmed = window.confirm(
        "Bạn có các chỉnh sửa cấu hình chưa lưu trong Agent Studio Workshop. Bạn có chắc chắn muốn hủy và đóng?"
      );
      if (!confirmed) return;
    }
    validationScope.current.invalidate();
    conflictWorkbenchRef.current = null;
    setConflictWorkbench(null);
    setSaveError(null);
    onClose();
  }, [isSaving, isValidating, isDirty, onClose]);

  // Keyboard navigation for WAI-ARIA Tabs
  const handleTabKeyDown = (e: React.KeyboardEvent, currentTab: WorkshopTab) => {
    const tabs: WorkshopTab[] = ["persona", "model", "dependencies", "history"];
    const currentIndex = tabs.indexOf(currentTab);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      const nextTab = tabs[(currentIndex + 1) % tabs.length];
      setActiveTab(nextTab);
      document.getElementById(`workshop-tab-${nextTab}`)?.focus();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      const prevTab = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
      setActiveTab(prevTab);
      document.getElementById(`workshop-tab-${prevTab}`)?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveTab(tabs[0]);
      document.getElementById(`workshop-tab-${tabs[0]}`)?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveTab(tabs[tabs.length - 1]);
      document.getElementById(`workshop-tab-${tabs[tabs.length - 1]}`)?.focus();
    }
  };

  // Validation staleness logic (P0-02)
  const validationIsStale =
    validationResult !== null &&
    (validationResult.agent_id !== activeDraft?.agent_id ||
      isValidationResultStale(
        validationResult.revision,
        activeDraft?.revision ?? 1,
        hasEditsSinceValidation
      ));
  const validationReady = validationResult?.ready === true && !validationIsStale;

  const categorizedDiagnostics = useMemo(() => {
    if (!validationResult || !validationResult.diagnostics) return [];
    return categorizeDiagnostics(validationResult.diagnostics);
  }, [validationResult]);

  const criticalIssuesCount = useMemo(
    () => categorizedDiagnostics.filter((d) => d.severity === "critical").length,
    [categorizedDiagnostics]
  );
  const warningIssuesCount = useMemo(
    () => categorizedDiagnostics.filter((d) => d.severity === "warning").length,
    [categorizedDiagnostics]
  );

  const handleFocusDiagnosticField = (diagnostic: CategorizedDiagnostic) => {
    setActiveTab(diagnostic.tabTarget);
    setTimeout(() => {
      let elementId = "";
      if (diagnostic.field.includes("persona") || diagnostic.field.includes("instruction")) {
        elementId = "edit-instructions";
      } else if (diagnostic.field.includes("model")) {
        elementId = "edit-model-ref";
      } else if (diagnostic.field.includes("mode")) {
        elementId = "edit-mode-select";
      } else if (diagnostic.field.includes("step")) {
        elementId = "edit-max-steps-input";
      } else if (diagnostic.field.includes("time") || diagnostic.field.includes("wall")) {
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

  // Validate handler
  const handleValidateDraft = async () => {
    if (!activeDraft || !isOpen) return;
    const editor = editorLifetime.current.capture();
    setIsValidating(true);
    setValidationError(null);
    setValidationResult(null);

    const localEdits: AgentDraftEdits = {
      mode,
      modelRef: modelRef.trim(),
      instructions: instructions.trim(),
      maxSteps: Number(maxSteps),
      maxWallTimeSeconds: Number(maxWallTime),
      maxTotalTokens: Number(maxTokens),
    };

    const updatedSpec = mergeAgentDraftEdits(activeDraft.spec, localEdits);

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
      if (!editor.isCurrent() || !validation.isCurrent()) return;
      if (!validation.matchesResponse(result)) {
        throw new Error("Kết quả kiểm tra không khớp Agent hoặc revision hiện tại.");
      }
      setValidationResult(result);
      setHasEditsSinceValidation(false);
      if (result.ready) {
        onShowToast("Cấu hình hợp lệ và sẵn sàng công bố.", "success");
      } else {
        onShowToast("Cấu hình chưa sẵn sàng (có phát hiện lỗi phụ thuộc).", "error");
      }
    } catch (err: unknown) {
      if (!editor.isCurrent() || !validation.isCurrent()) return;
      if (err instanceof ApiClientError) {
        setValidationError(err.message || "Lỗi kiểm tra cấu hình.");
      } else {
        setValidationError(
          err instanceof Error ? err.message : "Lỗi không xác định khi kiểm tra cấu hình."
        );
      }
    } finally {
      if (editor.isCurrent() && validation.isCurrent()) {
        setIsValidating(false);
      }
    }
  };

  // Direct Save / Patch handler (RF04-01: preserve newer edits while rebasing committed revision/base)
  const executeSave = async (
    specToSave: Record<string, unknown>,
    ifMatchRevision: number,
    conflictBaseSpec?: Record<string, unknown> | null
  ) => {
    if (!activeDraft || !isOpen) return;
    const editor = editorLifetime.current.capture();
    saveScope.current.reset();
    const saving = saveScope.current.capture();
    const isCurrent = () => editor.isCurrent() && saving.isCurrent();
    const submitGeneration = editGenerationRef.current;
    setIsSaving(true);
    setSaveError(null);

    try {
      const saved = await defaultApiClient.replaceAgentDraftSpec(
        workspaceId,
        activeDraft.agent_id,
        { spec: specToSave },
        ifMatchRevision,
        saving.signal
      );

      if (!isCurrent()) return;
      if (saved.agent_id !== activeDraft.agent_id) {
        throw new Error("Save response does not match the selected Agent");
      }

      // Successfully saved! Always advance baseSpec and committed revision in parent.
      const clonedSavedSpec = JSON.parse(JSON.stringify(saved.spec)) as Record<string, unknown>;
      baseSpecRef.current = clonedSavedSpec;
      setBaseSpec(clonedSavedSpec);

      const hasNewerEdits = editGenerationRef.current !== submitGeneration;
      if (!hasNewerEdits) {
        const freshEdits = extractEditsFromSpec(saved.spec);
        latestEditsRef.current = { ...freshEdits };
        setInstructions(freshEdits.instructions);
        setModelRef(freshEdits.modelRef);
        setMode(freshEdits.mode as ExecutionMode);
        setMaxSteps(freshEdits.maxSteps);
        setMaxWallTime(freshEdits.maxWallTimeSeconds);
        setMaxTokens(freshEdits.maxTotalTokens);
      }

      conflictWorkbenchRef.current = null;
      setConflictWorkbench(null);
      setSaveError(null);
      invalidateValidation();
      setValidationResult(null);
      setHasEditsSinceValidation(hasNewerEdits);

      onDraftSaved(saved);
      onShowToast(
        hasNewerEdits
          ? `Đã lưu thay đổi (revision ${saved.revision}). Các chỉnh sửa mới hơn vẫn được giữ nguyên chưa lưu.`
          : `Đã lưu thay đổi thành công (revision ${saved.revision}).`,
        "success"
      );
    } catch (err: unknown) {
      if (!isCurrent()) return;
      if (err instanceof ApiClientError) {
        if (err.status === 412) {
          // HTTP 412: Fetch fresh server draft and calculate 3-way diff (P0-01 + RF04-01)
          try {
            const freshServerDraft = await defaultApiClient.getAgentDraft(
              workspaceId,
              activeDraft.agent_id,
              saving.signal
            );
            if (!isCurrent()) return;
            if (freshServerDraft.agent_id !== activeDraft.agent_id) {
              throw new Error("Conflict response does not match the selected Agent");
            }

            const currentLocalEdits: AgentDraftEdits =
              editGenerationRef.current !== submitGeneration
                ? {
                    mode: latestEditsRef.current.mode,
                    modelRef: latestEditsRef.current.modelRef.trim(),
                    instructions: latestEditsRef.current.instructions.trim(),
                    maxSteps: Number(latestEditsRef.current.maxSteps),
                    maxWallTimeSeconds: Number(latestEditsRef.current.maxWallTimeSeconds),
                    maxTotalTokens: Number(latestEditsRef.current.maxTotalTokens),
                  }
                : extractEditsFromSpec(specToSave);

            const diffResult = calculateThreeWayDraftDiff(
              conflictBaseSpec ?? baseSpecRef.current ?? baseSpec,
              currentLocalEdits,
              freshServerDraft.spec
            );

            const initialResolutions: Record<keyof AgentDraftEdits, "local" | "server"> = {
              instructions: "local",
              modelRef: "local",
              mode: "local",
              maxSteps: "local",
              maxWallTimeSeconds: "local",
              maxTotalTokens: "local",
            };

            const nextConflict: ConflictWorkbenchState = {
              serverDraft: freshServerDraft,
              diffResult,
              userResolutions: initialResolutions,
            };
            conflictWorkbenchRef.current = nextConflict;
            setConflictWorkbench(nextConflict);
            return;
          } catch {
            if (!isCurrent()) return;
            setSaveError(
              "Xung đột sửa đổi (HTTP 412): Không thể tải phiên bản máy chủ mới nhất để đối soát."
            );
            return;
          }
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
          setSaveError(err.message || "Lỗi kiểm tra tính hợp lệ của schema.");
          return;
        }
        setSaveError(err.message || "Lỗi máy chủ khi cập nhật bản nháp.");
      } else {
        setSaveError(err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định.");
      }
    } finally {
      if (isCurrent()) setIsSaving(false);
    }
  };

  const handleSaveDraftSpec = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeDraft || isSaving || conflictWorkbench) return;

    const trimmed = instructions.trim();
    if (!trimmed) {
      setSaveError("Chỉ lệnh Persona không được để trống.");
      setActiveTab("persona");
      return;
    }

    const currentEdits: AgentDraftEdits = {
      mode,
      modelRef: modelRef.trim(),
      instructions: trimmed,
      maxSteps: Number(maxSteps),
      maxWallTimeSeconds: Number(maxWallTime),
      maxTotalTokens: Number(maxTokens),
    };

    const fullSpec = mergeAgentDraftEdits(activeDraft.spec, currentEdits);
    await executeSave(fullSpec, activeDraft.revision);
  };

  // P0-01: Discard local changes and adopt server draft
  const handleAdoptServerDraft = () => {
    if (!conflictWorkbench || isSaving) return;
    const serverDraft = conflictWorkbench.serverDraft;
    const clonedSpec = JSON.parse(JSON.stringify(serverDraft.spec)) as Record<string, unknown>;
    const serverEdits = extractEditsFromSpec(serverDraft.spec);

    baseSpecRef.current = clonedSpec;
    latestEditsRef.current = { ...serverEdits };
    editGenerationRef.current += 1;
    conflictWorkbenchRef.current = null;

    setBaseSpec(clonedSpec);
    setInstructions(serverEdits.instructions);
    setModelRef(serverEdits.modelRef);
    setMode(serverEdits.mode as ExecutionMode);
    setMaxSteps(serverEdits.maxSteps);
    setMaxWallTime(serverEdits.maxWallTimeSeconds);
    setMaxTokens(serverEdits.maxTotalTokens);

    setConflictWorkbench(null);
    setSaveError(null);
    invalidateValidation();
    setValidationResult(null);
    setHasEditsSinceValidation(false);
    onDraftSaved(serverDraft);
    onShowToast(`Đã đồng bộ bản nháp mới nhất (revision ${serverDraft.revision}) từ máy chủ.`, "info");
  };

  // P0-01: Save 3-Way Merged Spec with server revision as If-Match
  const handleSaveMergedSpec = async () => {
    if (!conflictWorkbench || isSaving) return;
    const { serverDraft, diffResult, userResolutions } = conflictWorkbench;

    // Build the resolved edits based on diffResult and user choices for conflicting fields
    const finalEdits: AgentDraftEdits = { ...diffResult.autoMergedEdits };

    for (const conflict of diffResult.conflictingFields) {
      const field = conflict.field;
      const choice = userResolutions[field] || "local";
      if (choice === "server") {
        if (field === "maxSteps" || field === "maxWallTimeSeconds" || field === "maxTotalTokens") {
          finalEdits[field] = Number(conflict.serverValue);
        } else {
          finalEdits[field] = conflict.serverValue;
        }
      } else {
        if (field === "maxSteps" || field === "maxWallTimeSeconds" || field === "maxTotalTokens") {
          finalEdits[field] = Number(conflict.localValue);
        } else {
          finalEdits[field] = conflict.localValue;
        }
      }
    }

    const mergedSpec = buildThreeWayMergedSpec(serverDraft.spec, finalEdits);
    await executeSave(mergedSpec, serverDraft.revision, serverDraft.spec);
  };

  // Line numbers and metrics
  const lineNumbers = useMemo(() => {
    const count = Math.max(1, instructions.split("\n").length);
    return Array.from({ length: count }, (_, i) => i + 1);
  }, [instructions]);

  const tokenEstimate = useMemo(() => estimateTokenCount(instructions), [instructions]);
  const promptSafety = useMemo(() => detectPromptSafetyRisk(instructions), [instructions]);

  const activeDependencies = useMemo(() => {
    if (!activeDraft?.spec) return { tools: [], skills: [], knowledge: [] };
    const spec = activeDraft.spec;
    const tools = Array.isArray(spec.toolRefs) ? spec.toolRefs : [];
    const skills = Array.isArray(spec.skillRefs) ? spec.skillRefs : [];
    const knowledge = Array.isArray(spec.knowledgeRefs) ? spec.knowledgeRefs : [];
    return { tools, skills, knowledge };
  }, [activeDraft]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleSafeClose}
      title={`Agent Studio Workshop: ${activeDraft?.display_name || ""}`}
      description={`Định danh: ${activeDraft?.slug} | Bản nháp máy chủ: revision ${activeDraft?.revision ?? 1}`}
      maxWidth="920px"
      footer={
        <>
          <Button
            variant="secondary"
            size="md"
            type="button"
            onClick={handleSafeClose}
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
            disabled={isSaving || Boolean(conflictWorkbench)}
            title="Đối soát ràng buộc và dependencies tại Step 04-B"
            aria-label="Kiểm tra cấu hình AgentSpec tại Step 04-B"
          >
            Kiểm tra cấu hình (Validate)
          </Button>
          <Button
            variant="primary"
            size="md"
            type="submit"
            form="edit-agent-form"
            isLoading={isSaving}
            disabled={isValidating || Boolean(conflictWorkbench)}
            aria-label="Lưu các thay đổi cấu hình AgentSpec"
          >
            Lưu thay đổi (Save Spec)
          </Button>
        </>
      }
    >
      <div className="workshop-container">
        {/* Diagnostic Center (04-B) */}
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
                  <span className="diagnostic-badge-success">✓ SẴN SÀNG CÔNG BỐ</span>
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
                disabled={isValidating || isSaving || Boolean(conflictWorkbench)}
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
                              aria-label={`Nhảy tới trường dữ liệu ${d.field}`}
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
                              aria-label={`Nhảy tới trường dữ liệu ${d.field}`}
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

        {/* 3-Way Conflict Resolution Workbench (P0-01) */}
        {conflictWorkbench && (
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
                    Bản nháp trên máy chủ đã được nâng lên <strong>rev {conflictWorkbench.serverDraft.revision}</strong>.
                    Hệ thống đã tự động giữ lại các trường không sửa đổi và trường thay đổi độc lập từ máy chủ.
                    {conflictWorkbench.diffResult.hasConflict && (
                      <span> Vui lòng chọn giá trị muốn giữ cho các trường có xung đột trực tiếp.</span>
                    )}
                  </p>
                </div>
              </div>

              <div className="conflict-decision-actions">
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={handleAdoptServerDraft}
                  disabled={isSaving}
                  title="Hủy bỏ thay đổi cục bộ và tải toàn bộ bản máy chủ mới nhất"
                  aria-label="Áp dụng bản Server mới nhất và hủy bỏ thay đổi cục bộ"
                >
                  Áp dụng bản Server
                </Button>
                <Button
                  variant="copper"
                  size="sm"
                  type="button"
                  onClick={handleSaveMergedSpec}
                  isLoading={isSaving}
                  disabled={isSaving}
                  title="Lưu bản hợp nhất an toàn với If-Match là revision mới từ máy chủ"
                  aria-label="Lưu bản hợp nhất an toàn với If-Match là revision mới từ máy chủ"
                >
                  Lưu bản hợp nhất (Save Merged Spec)
                </Button>
              </div>
            </div>

            {/* 3-Way Diff Table */}
            <div
              className="conflict-diff-table"
              role="table"
              aria-label="Bảng so sánh chi tiết giữa bản Server và bản sửa đổi cục bộ"
            >
              <div className="diff-table-header" role="row">
                <span className="diff-col-field" role="columnheader">Trường dữ liệu</span>
                <span className="diff-col-server" role="columnheader">
                  Bản Server (rev {conflictWorkbench.serverDraft.revision})
                </span>
                <span className="diff-col-local" role="columnheader">
                  Bản sửa đổi của bạn (rev {activeDraft?.revision ?? 1})
                </span>
                <span className="diff-col-status" role="columnheader">Trạng thái / Lựa chọn</span>
              </div>
              {conflictWorkbench.diffResult.diffs.map((diff) => {
                const resolution = conflictWorkbench.userResolutions[diff.field] || "local";
                return (
                  <div
                    key={diff.field}
                    className={`diff-table-row ${
                      diff.isConflict
                        ? "row-highlight-conflict"
                        : diff.hasLocalChange || diff.hasServerChange
                        ? "row-highlight-diff"
                        : ""
                    }`}
                    role="row"
                  >
                    <span className="diff-cell-field" role="cell">
                      {diff.label}
                    </span>
                    <span className="diff-cell-server" role="cell">
                      {diff.serverValue || "—"}
                    </span>
                    <span className="diff-cell-local" role="cell">
                      {diff.localValue || "—"}
                    </span>
                    <span className="diff-cell-action" role="cell">
                      {diff.isConflict ? (
                        <div className="conflict-choice-toggle" role="group" aria-label={`Lựa chọn cho ${diff.label}`}>
                          <button
                            type="button"
                            disabled={isSaving}
                            className={`choice-btn ${resolution === "local" ? "choice-active" : ""}`}
                            onClick={() =>
                              setConflictWorkbench((prev) => {
                                const next = prev
                                  ? {
                                      ...prev,
                                      userResolutions: {
                                        ...prev.userResolutions,
                                        [diff.field]: "local" as const,
                                      },
                                    }
                                  : null;
                                conflictWorkbenchRef.current = next;
                                return next;
                              })
                            }
                            aria-pressed={resolution === "local"}
                          >
                            Dùng bản của tôi
                          </button>
                          <button
                            type="button"
                            disabled={isSaving}
                            className={`choice-btn ${resolution === "server" ? "choice-active" : ""}`}
                            onClick={() =>
                              setConflictWorkbench((prev) => {
                                const next = prev
                                  ? {
                                      ...prev,
                                      userResolutions: {
                                        ...prev.userResolutions,
                                        [diff.field]: "server" as const,
                                      },
                                    }
                                  : null;
                                conflictWorkbenchRef.current = next;
                                return next;
                              })
                            }
                            aria-pressed={resolution === "server"}
                          >
                            Dùng bản máy chủ
                          </button>
                        </div>
                      ) : diff.hasServerChange ? (
                        <span className="diff-tag-auto-server">Tự động nhận từ Server</span>
                      ) : diff.hasLocalChange ? (
                        <span className="diff-tag-auto-local">Giữ thay đổi của bạn</span>
                      ) : (
                        <span className="diff-tag-unchanged">Không đổi</span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {saveError && (
          <div className="form-error-banner" role="alert" aria-live="assertive">
            <span aria-hidden="true">✕</span> {saveError}
          </div>
        )}

        {/* Workshop Navigation Tabs */}
        <div
          className="workshop-nav-tabs"
          role="tablist"
          aria-label="Các phân hệ cấu hình Tác tử AI trong Workshop"
        >
          <button
            id="workshop-tab-persona"
            type="button"
            role="tab"
            aria-selected={activeTab === "persona"}
            aria-controls="workshop-tabpanel-persona"
            tabIndex={activeTab === "persona" ? 0 : -1}
            className={`workshop-tab-btn ${activeTab === "persona" ? "tab-active" : ""}`}
            onClick={() => setActiveTab("persona")}
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
            aria-selected={activeTab === "model"}
            aria-controls="workshop-tabpanel-model"
            tabIndex={activeTab === "model" ? 0 : -1}
            className={`workshop-tab-btn ${activeTab === "model" ? "tab-active" : ""}`}
            onClick={() => setActiveTab("model")}
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
            aria-selected={activeTab === "dependencies"}
            aria-controls="workshop-tabpanel-dependencies"
            tabIndex={activeTab === "dependencies" ? 0 : -1}
            className={`workshop-tab-btn ${activeTab === "dependencies" ? "tab-active" : ""}`}
            onClick={() => setActiveTab("dependencies")}
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

          <button
            id="workshop-tab-history"
            type="button"
            role="tab"
            aria-selected={activeTab === "history"}
            aria-controls="workshop-tabpanel-history"
            tabIndex={activeTab === "history" ? 0 : -1}
            className={`workshop-tab-btn ${activeTab === "history" ? "tab-active" : ""}`}
            onClick={() => {
              setActiveTab("history");
              onRefreshHistory();
            }}
            onKeyDown={(e) => handleTabKeyDown(e, "history")}
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
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span>4. Lịch sử & Môi trường ({historyVersions.length})</span>
          </button>
        </div>

        <form id="edit-agent-form" onSubmit={handleSaveDraftSpec} className="workshop-form-content">
          {/* TAB 1: PERSONA & PROMPT */}
          {activeTab === "persona" && (
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
                  <span className="agent-rev-token">rev {activeDraft?.revision ?? 1}</span>
                </div>
                <div className="meta-info-item">
                  <span className="meta-label">Ước tính Token:</span>
                  <span className="meta-token-count">
                    ~{tokenEstimate} tokens ({instructions.length} ký tự)
                  </span>
                </div>
                <div className="meta-info-item">
                  {promptSafety.hasRisk ? (
                    <span className="safety-badge-warning" title={promptSafety.reason}>
                      ⚠️ Cảnh báo Tiền đề: {promptSafety.reason}
                    </span>
                  ) : (
                    <span className="safety-badge-safe">
                      🛡️ Phân tích sơ bộ: Chưa phát hiện từ khóa rủi ro
                    </span>
                  )}
                </div>
              </div>

              <div className="persona-editor-wrapper">
                <div ref={gutterRef} className="editor-line-gutter" aria-hidden="true">
                  {lineNumbers.map((num) => (
                    <div key={num} className="line-num">
                      {num}
                    </div>
                  ))}
                </div>

                <textarea
                  id="edit-instructions"
                  rows={14}
                  value={instructions}
                  onChange={(e) => updateInstructions(e.target.value)}
                  onScroll={handleTextareaScroll}
                  disabled={isFormLocked}
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
                  💡 <strong>Mẹo kiến trúc Persona:</strong> Định nghĩa rõ ràng vai trò, ngữ cảnh doanh nghiệp, giới hạn phạm vi gọi công cụ và định dạng phản hồi đầu ra (JSON/Markdown).
                </span>
              </div>
            </div>
          )}

          {/* TAB 2: MODEL & RUNTIME BOUNDS */}
          {activeTab === "model" && (
            <div
              id="workshop-tabpanel-model"
              role="tabpanel"
              aria-labelledby="workshop-tab-model"
              tabIndex={0}
              className="tab-pane-model"
            >
              <div className="model-selection-grid">
                <div className="form-field-group">
                  <label htmlFor="edit-model-ref" className="form-field-label">
                    Tham chiếu Mô hình (Model Config UUID) *
                  </label>
                  <input
                    id="edit-model-ref"
                    type="text"
                    className="form-control-input"
                    value={modelRef}
                    onChange={(e) => updateModelRef(e.target.value)}
                    disabled={isFormLocked}
                    placeholder="Ví dụ UUID: 00000000-0000-0000-0000-000000000000"
                    required
                    aria-required="true"
                    aria-label="Tham chiếu định danh cấu hình Model (UUID)"
                  />
                  <span className="field-hint-caption">
                    Nhập UUID của Model Config đang hoạt động trong Workspace này để vượt qua bước kiểm tra và xuất bản.
                    Bản nháp có tham chiếu chưa phân giải vẫn có thể lưu để tiếp tục chỉnh sửa.
                  </span>
                </div>

                <div className="form-field-group">
                  <label htmlFor="edit-mode-select" className="form-field-label">
                    Chế độ thực thi (Execution Mode)
                  </label>
                  <select
                    id="edit-mode-select"
                    value={mode}
                    onChange={(e) => updateMode(e.target.value as ExecutionMode)}
                    disabled={isFormLocked}
                    className="form-select-control"
                    aria-label="Lựa chọn chế độ thực thi"
                  >
                    <option value="fixed_plan">fixed_plan (Thực thi kế hoạch tuần tự — Đang hỗ trợ)</option>
                    <option value="planner_based" disabled>
                      planner_based (Chưa sẵn sàng ở phiên bản hiện tại)
                    </option>
                    <option value="react" disabled>
                      react (Chưa sẵn sàng ở phiên bản hiện tại)
                    </option>
                  </select>
                  <span className="field-hint-caption">
                    {mode === "fixed_plan" && "Kiểm soát luồng chạy nghiêm ngặt theo các bước định sẵn."}
                    {mode === "planner_based" && "Chế độ planner_based chưa được hỗ trợ để xuất bản ở phiên bản hiện tại; hãy chuyển sang fixed_plan khi sẵn sàng."}
                    {mode === "react" && "Chế độ react chưa được hỗ trợ để xuất bản ở phiên bản hiện tại; hãy chuyển sang fixed_plan khi sẵn sàng."}
                  </span>
                </div>
              </div>

              {/* Runtime Bounds with Sliders + Number Inputs (P0-02: Preset buttons now invalidate validation) */}
              <div className="runtime-bounds-container">
                <h4 className="bounds-section-title">Ranh giới tài nguyên vận hành (Runtime Bounds)</h4>

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
                      value={maxSteps}
                      onChange={(e) => updateMaxSteps(Number(e.target.value))}
                      disabled={isFormLocked}
                      aria-label="Nhập số bước tối đa (từ 1 đến 50)"
                      className="bound-number-input"
                    />
                  </div>
                  <input
                    id="edit-max-steps-slider"
                    type="range"
                    min={1}
                    max={50}
                    value={maxSteps}
                    onChange={(e) => updateMaxSteps(Number(e.target.value))}
                    disabled={isFormLocked}
                    aria-label="Thanh trượt điều chỉnh Số bước tối đa"
                    aria-valuenow={maxSteps}
                    aria-valuemin={1}
                    aria-valuemax={50}
                    aria-valuetext={`${maxSteps} bước`}
                    aria-controls="edit-max-steps-input"
                    className="bound-slider-input"
                  />
                  <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị số bước">
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxSteps(10)}
                      aria-label="Chọn mốc an toàn 10 bước"
                    >
                      [10] Khuyến nghị Safe
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxSteps(25)}
                      aria-label="Chọn mốc tiêu chuẩn 25 bước"
                    >
                      [25] Chuẩn Standard
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxSteps(50)}
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
                      value={maxWallTime}
                      onChange={(e) => updateMaxWallTime(Number(e.target.value))}
                      disabled={isFormLocked}
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
                    value={maxWallTime}
                    onChange={(e) => updateMaxWallTime(Number(e.target.value))}
                    disabled={isFormLocked}
                    aria-label="Thanh trượt điều chỉnh Thời gian chạy tối đa"
                    aria-valuenow={maxWallTime}
                    aria-valuemin={10}
                    aria-valuemax={600}
                    aria-valuetext={`${maxWallTime} giây`}
                    aria-controls="edit-max-walltime-input"
                    className="bound-slider-input"
                  />
                  <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị thời gian chạy">
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxWallTime(60)}
                      aria-label="Chọn mốc tác vụ nhanh 60 giây"
                    >
                      [60s] Tác vụ nhanh
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxWallTime(180)}
                      aria-label="Chọn mốc tiêu chuẩn 180 giây"
                    >
                      [180s] Tiêu chuẩn 3m
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxWallTime(300)}
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
                      value={maxTokens}
                      onChange={(e) => updateMaxTokens(Number(e.target.value))}
                      disabled={isFormLocked}
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
                    value={maxTokens}
                    onChange={(e) => updateMaxTokens(Number(e.target.value))}
                    disabled={isFormLocked}
                    aria-label="Thanh trượt điều chỉnh Ngân sách Token tối đa"
                    aria-valuenow={maxTokens}
                    aria-valuemin={1000}
                    aria-valuemax={128000}
                    aria-valuetext={`${maxTokens} tokens`}
                    aria-controls="edit-max-tokens-input"
                    className="bound-slider-input"
                  />
                  <div className="slider-ticks-row" role="group" aria-label="Mốc khuyến nghị ngân sách Token">
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxTokens(8000)}
                      aria-label="Chọn mốc tiết kiệm 8,000 tokens"
                    >
                      [8k] Tiết kiệm
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxTokens(16000)}
                      aria-label="Chọn mốc chuẩn 16,000 tokens"
                    >
                      [16k] Chuẩn
                    </button>
                    <button
                      type="button"
                      disabled={isFormLocked}
                      className="tick-mark"
                      onClick={() => updateMaxTokens(64000)}
                      aria-label="Chọn mốc ngữ cảnh sâu 64,000 tokens"
                    >
                      [64k] Ngữ cảnh sâu
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: DEPENDENCIES & BINDINGS (P1-04: Truthful labels) */}
          {activeTab === "dependencies" && (
            <div
              id="workshop-tabpanel-dependencies"
              role="tabpanel"
              aria-labelledby="workshop-tab-dependencies"
              tabIndex={0}
              className="tab-pane-dependencies"
            >
              <div className="dependencies-overview-box">
                <h4 className="dependencies-title">Liên kết Công cụ, Kỹ năng & Cơ sở Tri thức</h4>
                <p className="dependencies-desc">
                  Tác tử AI liên kết các phụ thuộc ngoại vi thông qua cấu hình AgentSpec.
                  Khi xuất bản, các phụ thuộc sẽ được kiểm tra phân quyền và khóa bất biến.
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
                      Chưa liên kết công cụ nào. Tác tử đang chạy ở chế độ thuần mô hình (Prompt Only).
                    </div>
                  ) : (
                    <div className="dep-cards-grid" role="list" aria-label="Danh sách công cụ Tools liên kết">
                      {activeDependencies.tools.map((t) => (
                        <div key={String(t)} className="dep-item-card" role="listitem">
                          <span className="dep-item-name">{String(t)}</span>
                          <span className="dep-status-badge badge-declared">Liên kết trong Spec</span>
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
                    <div className="dep-empty-card">Chưa liên kết kỹ năng nào.</div>
                  ) : (
                    <div className="dep-cards-grid" role="list" aria-label="Danh sách kỹ năng Skills liên kết">
                      {activeDependencies.skills.map((s) => (
                        <div key={String(s)} className="dep-item-card" role="listitem">
                          <span className="dep-item-name">{String(s)}</span>
                          <span className="dep-status-badge badge-declared">Liên kết trong Spec</span>
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
                    <div className="dep-empty-card">Chưa liên kết cơ sở tri thức nào.</div>
                  ) : (
                    <div className="dep-cards-grid" role="list" aria-label="Danh sách cơ sở tri thức Knowledge Bases liên kết">
                      {activeDependencies.knowledge.map((k) => (
                        <div key={String(k)} className="dep-item-card" role="listitem">
                          <span className="dep-item-name">{String(k)}</span>
                          <span className="dep-status-badge badge-declared">Liên kết trong Spec</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: VERSION HISTORY & ENVIRONMENT POINTERS (04-D) */}
          {activeTab === "history" && (
            <div
              id="workshop-tabpanel-history"
              role="tabpanel"
              aria-labelledby="workshop-tab-history"
              tabIndex={0}
              className="tab-pane-history"
            >
              <div className="history-arch-banner">
                <div className="arch-banner-title">
                  <svg
                    aria-hidden="true"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="16" x2="12" y2="12" />
                    <line x1="12" y1="8" x2="12.01" y2="8" />
                  </svg>
                  <strong>Cơ chế Phân tách Xuất bản & Điều chuyển Môi trường (ADR-022)</strong>
                </div>
                <p className="arch-banner-text">
                  Xuất bản (Publish) tạo ra một phiên bản bất biến (v1, v2...) kèm mã băm nội dung RFC 8785.
                  Điều chuyển (Promote/Rollback) sử dụng con trỏ CAS để gán phiên bản cho từng môi trường (Development, Staging, Production).
                  Quay lui (Rollback) chọn lại phiên bản cũ mà không bao giờ thay đổi dữ liệu lịch sử đã xuất bản.
                </p>
              </div>

              {/* Environment Pointers Grid */}
              <div className="env-pointers-grid">
                {(["development", "staging", "production"] as const).map((env) => {
                  const pub = historyPublications.find((p) => p.environment === env);
                  const envLabels = {
                    development: "Phát triển (Development)",
                    staging: "Kiểm thử (Staging)",
                    production: "Sản xuất (Production)",
                  };
                  return (
                    <div
                      key={env}
                      className={`env-pointer-card ${pub ? "env-active" : "env-empty"}`}
                    >
                      <div className="env-header">
                        <span className="env-name">{envLabels[env]}</span>
                        {isLoadingHistory || historyError ? (
                          <span className="env-status-badge badge-idle">Chưa xác nhận</span>
                        ) : pub ? (
                          <span className="env-status-badge badge-active">Hoạt động</span>
                        ) : (
                          <span className="env-status-badge badge-idle">Chưa gán</span>
                        )}
                      </div>
                      <div className="env-body">
                        {isLoadingHistory || historyError ? (
                          <div className="env-none">
                            {historyError ? "Không tải được trạng thái môi trường." : "Đang tải trạng thái môi trường..."}
                          </div>
                        ) : pub ? (
                          <>
                            <div className="env-version">v{pub.version}</div>
                            <div className="env-revision">Con trỏ sửa đổi: rev #{pub.revision}</div>
                            <div className="env-sha" title={pub.bundle_sha256}>
                              SHA: {pub.bundle_sha256.substring(0, 12)}...
                            </div>
                          </>
                        ) : (
                          <div className="env-none">
                            Chưa có phiên bản nào được điều chuyển tới môi trường này.
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Version History Table / List */}
              <div className="history-table-container">
                <div className="history-table-header">
                  <h4 className="history-table-title">
                    Lịch sử Phiên bản Bất biến ({historyVersions.length})
                  </h4>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={onRefreshHistory}
                    isLoading={isLoadingHistory}
                    type="button"
                  >
                    Tải lại (Refresh)
                  </Button>
                </div>

                {isLoadingHistory ? (
                  <div className="history-loading">Đang tải lịch sử phiên bản...</div>
                ) : historyError ? (
                  <div className="history-error-card" role="alert">
                    <span>{historyError}</span>
                    <Button variant="secondary" size="sm" onClick={onRefreshHistory} type="button">
                      Thử lại
                    </Button>
                  </div>
                ) : historyVersions.length === 0 ? (
                  <div className="history-empty-card">
                    Chưa có phiên bản nào được xuất bản. Nhấn nút <strong>Publish</strong> ở góc trên hoặc ngoài danh sách để tạo phiên bản đầu tiên.
                  </div>
                ) : (
                  <div className="history-versions-list" role="list" aria-label="Danh sách phiên bản đã phát hành">
                    {historyVersions.map((v) => {
                      const activeEnvs = historyPublications.filter(
                        (p) => p.agent_version_id === v.agent_version_id
                      );
                      return (
                        <div key={v.agent_version_id} className="history-version-row" role="listitem">
                          <div className="version-info-col">
                            <div className="version-tag-line">
                              <span className="version-badge">v{v.version}</span>
                              <span className="version-immutable-pill">Immutable (Bất biến)</span>
                              {activeEnvs.map((ae) => (
                                <span
                                  key={ae.environment}
                                  className={`env-deployed-badge env-${ae.environment}`}
                                >
                                  {ae.environment.toUpperCase()}
                                </span>
                              ))}
                            </div>
                            <div className="version-meta-line">
                              <span>Mô hình: {v.model_name || "Mặc định"}</span>
                              <span>•</span>
                              <span>Ngày xuất bản: {new Date(v.created_at).toLocaleString("vi-VN")}</span>
                            </div>
                            <div className="version-hash-line">
                              <span className="hash-label">RFC 8785 SHA-256:</span>
                              <code className="hash-code">{v.bundle_sha256}</code>
                              <button
                                type="button"
                                className="hash-copy-btn"
                                onClick={() =>
                                  onCopyText(v.bundle_sha256, `sha-${v.agent_version_id}`)
                                }
                                title="Sao chép mã băm đầy đủ"
                                aria-label={`Sao chép mã băm phiên bản ${v.version}`}
                              >
                                {copiedKey === `sha-${v.agent_version_id}` ? "Đã chép!" : "Chép"}
                              </button>
                            </div>
                          </div>
                          <div className="version-actions-col">
                            <Button
                              variant="secondary"
                              size="sm"
                              type="button"
                              onClick={() => onOpenPromoteModal(v)}
                              aria-label={`Điều chuyển hoặc quay lui phiên bản v${v.version}`}
                            >
                              Điều chuyển / Quay lui
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                    {historyNextCursor && (
                      <Button
                        variant="secondary"
                        type="button"
                        isLoading={isLoadingHistoryMore}
                        onClick={onLoadMoreHistory}
                      >
                        Tải phiên bản cũ hơn
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </form>
      </div>
    </Modal>
  );
}
