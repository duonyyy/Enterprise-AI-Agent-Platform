"use client";

import React, { useState, useCallback, useMemo } from "react";
import { Modal, Button, Input } from "@enterprise/ui";
import { CreateAgentDraftDTO } from "@enterprise/api-client";
import { slugifyAgentName } from "../../../../../lib/agent-draft-state";
import { ExecutionMode, StarterTemplate } from "./agent-list";

export interface AgentCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (dto: CreateAgentDraftDTO) => Promise<boolean>;
  isSubmitting: boolean;
  errorMessage?: string;
  onClearError: () => void;
  initialTemplate?: StarterTemplate | null;
}

export function AgentCreateModal({
  isOpen,
  onClose,
  onSubmit,
  isSubmitting,
  errorMessage,
  onClearError,
  initialTemplate,
}: AgentCreateModalProps) {
  const [name, setName] = useState(initialTemplate?.name ?? "");
  const [slug, setSlug] = useState(initialTemplate?.slug ?? "");
  const [instructions, setInstructions] = useState(initialTemplate?.instructions ?? "");
  const [modelRef, setModelRef] = useState(initialTemplate?.modelRef ?? "");
  const [mode, setMode] = useState<ExecutionMode>(initialTemplate?.mode ?? "fixed_plan");
  const [maxSteps, setMaxSteps] = useState<number | string>(initialTemplate?.maxSteps ?? 10);
  const [maxWallTime, setMaxWallTime] = useState<number | string>(initialTemplate?.maxWallTime ?? 180);
  const [maxTokens, setMaxTokens] = useState<number | string>(initialTemplate?.maxTokens ?? 16000);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // Sync with template when opened with a template
  React.useEffect(() => {
    if (initialTemplate) {
      setName(initialTemplate.name);
      setSlug(initialTemplate.slug);
      setInstructions(initialTemplate.instructions);
      setModelRef(initialTemplate.modelRef);
      setMode(initialTemplate.mode);
      setMaxSteps(initialTemplate.maxSteps);
      setMaxWallTime(initialTemplate.maxWallTime);
      setMaxTokens(initialTemplate.maxTokens);
      setValidationErrors({});
    }
  }, [initialTemplate]);

  const isDirty = useMemo(() => {
    return Boolean(
      name.trim() ||
        slug.trim() ||
        instructions.trim() ||
        modelRef.trim()
    );
  }, [name, slug, instructions, modelRef]);

  const handleNameChange = (val: string) => {
    setName(val);
    onClearError();
    setValidationErrors((prev) => {
      const next = { ...prev };
      delete next.name;
      delete next.slug;
      return next;
    });
    setSlug(slugifyAgentName(val));
  };

  const handleSlugChange = (val: string) => {
    setSlug(val);
    onClearError();
    setValidationErrors((prev) => {
      const next = { ...prev };
      delete next.slug;
      return next;
    });
  };

  const handleSafeClose = useCallback(() => {
    if (isSubmitting) return;
    if (isDirty) {
      const confirmed = window.confirm(
        "Bạn có thông tin chưa lưu trong biểu mẫu tạo tác tử. Bạn có chắc chắn muốn hủy và đóng?"
      );
      if (!confirmed) return;
    }
    setValidationErrors({});
    onClearError();
    onClose();
  }, [isSubmitting, isDirty, onClose, onClearError]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) return;

    const trimmedName = name.trim();
    const trimmedSlug = slug.trim();
    const trimmedInstructions = instructions.trim();
    const trimmedModelRef = modelRef.trim();

    const errors: Record<string, string> = {};

    if (!trimmedName) {
      errors.name = "Vui lòng nhập tên hiển thị của Tác tử AI.";
    }

    if (!trimmedSlug) {
      errors.slug = "Vui lòng nhập định danh Slug kỹ thuật.";
    } else if (!/^[a-z0-9_-]+$/.test(trimmedSlug)) {
      errors.slug = "Định danh Slug chỉ được chứa chữ thường không dấu, số, gạch ngang và gạch dưới.";
    }

    if (!trimmedInstructions) {
      errors.instructions = "Vui lòng nhập chỉ lệnh Persona (Persona Instructions).";
    }

    if (!trimmedModelRef) {
      errors.modelRef = "Vui lòng nhập tham chiếu Mô hình (Model Reference).";
    }

    const stepsNum = Number(maxSteps);
    if (!Number.isInteger(stepsNum) || isNaN(stepsNum) || stepsNum < 1 || stepsNum > 50) {
      errors.maxSteps = "Số bước tối đa phải là số nguyên từ 1 đến 50.";
    }

    const wallTimeNum = Number(maxWallTime);
    if (!Number.isInteger(wallTimeNum) || isNaN(wallTimeNum) || wallTimeNum < 10 || wallTimeNum > 600) {
      errors.maxWallTime = "Thời gian chạy tối đa phải từ 10 đến 600 giây.";
    }

    const tokensNum = Number(maxTokens);
    if (!Number.isInteger(tokensNum) || isNaN(tokensNum) || tokensNum < 1000 || tokensNum > 128000) {
      errors.maxTokens = "Ngân sách Token phải từ 1,000 đến 128,000 tokens.";
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }

    setValidationErrors({});

    const fullSpec = {
      schemaVersion: "1.0",
      kind: "Agent",
      metadata: {
        name: trimmedSlug,
        displayName: trimmedName,
      },
      mode,
      modelRef: trimmedModelRef,
      persona: {
        instructions: trimmedInstructions,
      },
      toolRefs: [],
      skillRefs: [],
      knowledgeRefs: [],
      limits: {
        maxSteps: stepsNum,
        maxWallTimeSeconds: wallTimeNum,
        maxTotalTokens: tokensNum,
      },
    };

    const success = await onSubmit({
      slug: trimmedSlug,
      display_name: trimmedName,
      spec: fullSpec,
    });

    if (success) {
      setName("");
      setSlug("");
      setInstructions("");
      setModelRef("");
      setMode("fixed_plan");
      setMaxSteps(10);
      setMaxWallTime(180);
      setMaxTokens(16000);
      setValidationErrors({});
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleSafeClose}
      title="Tạo Bản nháp Tác tử AI (Create Agent Draft)"
      description="Khởi tạo bản nháp với cấu hình AgentSpec đầy đủ. Ràng buộc mô hình và công cụ sẽ được xác thực tại Bước 04-B."
      maxWidth="560px"
      footer={
        <>
          <Button
            variant="secondary"
            size="md"
            type="button"
            onClick={handleSafeClose}
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
      <form id="create-agent-form" onSubmit={handleSubmit} className="modal-form-vertical">
        <Input
          id="new-agent-name"
          label="Tên hiển thị Agent (Display Name) *"
          placeholder="Ví dụ: Trợ lý Soạn thảo Báo giá"
          value={name}
          onChange={(e) => handleNameChange(e.target.value)}
          errorText={validationErrors.name}
          required
          autoFocus
        />

        <Input
          id="new-agent-slug"
          label="Định danh Kỹ thuật (Technical Slug) *"
          placeholder="tro-ly-soan-thao-bao-gia"
          value={slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          helperText="Chữ thường không dấu, số và gạch ngang (bất biến sau khi tạo)."
          errorText={validationErrors.slug}
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
            value={instructions}
            onChange={(e) => {
              setInstructions(e.target.value);
              onClearError();
              if (validationErrors.instructions) {
                setValidationErrors((prev) => {
                  const next = { ...prev };
                  delete next.instructions;
                  return next;
                });
              }
            }}
            required
            aria-required="true"
            className="form-textarea-control"
          />
          {validationErrors.instructions && (
            <span className="form-field-error" role="alert">
              {validationErrors.instructions}
            </span>
          )}
        </div>

        <Input
          id="new-model-ref"
          label="Tham chiếu Mô hình (Model Config UUID) *"
          placeholder="Ví dụ UUID: 00000000-0000-0000-0000-000000000000"
          value={modelRef}
          onChange={(e) => {
            setModelRef(e.target.value);
            onClearError();
            if (validationErrors.modelRef) {
              setValidationErrors((prev) => {
                const next = { ...prev };
                delete next.modelRef;
                return next;
              });
            }
          }}
          helperText="Nhập UUID của Model Config đang hoạt động trong Workspace để vượt qua bước kiểm tra và xuất bản (bản nháp vẫn có thể lưu khi chưa đủ phụ thuộc)."
          errorText={validationErrors.modelRef}
          required
        />

        <div className="form-row-2col">
          <div className="form-field-group">
            <label htmlFor="agent-mode" className="form-field-label">
              Chế độ thực thi (Mode)
            </label>
            <select
              id="agent-mode"
              value={mode}
              onChange={(e) => setMode(e.target.value as ExecutionMode)}
              className="form-select-control"
            >
              <option value="fixed_plan">fixed_plan (Đang hỗ trợ)</option>
              <option value="planner_based" disabled>
                planner_based (Chưa sẵn sàng)
              </option>
              <option value="react" disabled>
                react (Chưa sẵn sàng)
              </option>
            </select>
          </div>

          <div className="form-field-group">
            <Input
              id="new-max-steps"
              label="Số bước tối đa (Max Steps) *"
              type="number"
              min={1}
              max={50}
              value={maxSteps}
              onChange={(e) => {
                setMaxSteps(e.target.value);
                if (validationErrors.maxSteps) {
                  setValidationErrors((prev) => {
                    const next = { ...prev };
                    delete next.maxSteps;
                    return next;
                  });
                }
              }}
              errorText={validationErrors.maxSteps}
              required
            />
          </div>
        </div>

        <div className="form-row-2col">
          <div className="form-field-group">
            <Input
              id="new-max-walltime"
              label="Thời gian tối đa (giây) *"
              type="number"
              min={10}
              max={600}
              step={10}
              value={maxWallTime}
              onChange={(e) => {
                setMaxWallTime(e.target.value);
                if (validationErrors.maxWallTime) {
                  setValidationErrors((prev) => {
                    const next = { ...prev };
                    delete next.maxWallTime;
                    return next;
                  });
                }
              }}
              errorText={validationErrors.maxWallTime}
              required
            />
          </div>

          <div className="form-field-group">
            <Input
              id="new-max-tokens"
              label="Ngân sách Token tối đa *"
              type="number"
              min={1000}
              max={128000}
              step={1000}
              value={maxTokens}
              onChange={(e) => {
                setMaxTokens(e.target.value);
                if (validationErrors.maxTokens) {
                  setValidationErrors((prev) => {
                    const next = { ...prev };
                    delete next.maxTokens;
                    return next;
                  });
                }
              }}
              errorText={validationErrors.maxTokens}
              required
            />
          </div>
        </div>

        {errorMessage && (
          <div className="form-error-banner" role="alert" aria-live="assertive">
            <span aria-hidden="true">✕</span> {errorMessage}
          </div>
        )}
      </form>
    </Modal>
  );
}
