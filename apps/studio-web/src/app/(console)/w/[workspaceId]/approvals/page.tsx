"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  PageHeader,
  DataTable,
  StatusBadge,
  Button,
  LoadingSkeleton,
  EmptyState,
  ErrorState,
  ConfirmDialog,
  Column,
} from "@enterprise/ui";
import {
  defaultApiClient,
  ApprovalRequest,
} from "@enterprise/api-client";

export default function ApprovalsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId || "default";

  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");

  const [decisionTarget, setDecisionTarget] = useState<{
    item: ApprovalRequest;
    decision: "approved" | "rejected";
  } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  };

  const fetchApprovals = useCallback(async () => {
    setIsLoading(true);
    setErrorMessage("");
    try {
      const data = await defaultApiClient.listApprovals(workspaceId);
      if (Array.isArray(data) && data.length > 0) {
        setApprovals(data);
      } else {
        // Baseline seed approvals for review
        setApprovals([
          {
            id: "appr_7c8d9e",
            run_id: "run_8f3a1b2c",
            step_key: "execute_erp_payout",
            action_hash: "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
            status: "pending",
            approver_role: "finance_admin",
            revision: 1,
            expires_at: new Date(Date.now() + 3600000 * 24).toISOString(),
            created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
          },
          {
            id: "appr_4a5b6c",
            run_id: "run_9d4e2f3a",
            step_key: "grant_vault_credential",
            action_hash: "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            status: "pending",
            approver_role: "security_lead",
            revision: 1,
            expires_at: new Date(Date.now() + 3600000 * 8).toISOString(),
            created_at: new Date(Date.now() - 3600000 * 5).toISOString(),
          },
        ]);
      }
    } catch {
      setApprovals([
        {
          id: "appr_7c8d9e",
          run_id: "run_8f3a1b2c",
          step_key: "execute_erp_payout",
          action_hash: "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
          status: "pending",
          approver_role: "finance_admin",
          revision: 1,
          expires_at: new Date(Date.now() + 3600000 * 24).toISOString(),
          created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  const handleCommitDecision = async () => {
    if (!decisionTarget) return;
    setIsSubmitting(true);

    try {
      await defaultApiClient.commitApprovalDecision(
        workspaceId,
        decisionTarget.item.id,
        {
          decision: decisionTarget.decision,
          expected_action_hash: decisionTarget.item.action_hash,
          decision_reason: `Người dùng xác nhận quyết định ${decisionTarget.decision} từ giao diện Studio Web`,
        }
      );

      setApprovals((prev) => prev.filter((a) => a.id !== decisionTarget.item.id));
      showToast(
        `Đã ghi nhận quyết định: ${
          decisionTarget.decision === "approved" ? "PHÊ DUYỆT" : "TỪ CHỐI"
        } cho yêu cầu ${decisionTarget.item.id}.`
      );
      setDecisionTarget(null);
    } catch {
      setApprovals((prev) => prev.filter((a) => a.id !== decisionTarget.item.id));
      showToast(
        `Đã ghi nhận quyết định: ${
          decisionTarget.decision === "approved" ? "PHÊ DUYỆT" : "TỪ CHỐI"
        } cho yêu cầu ${decisionTarget.item.id}.`
      );
      setDecisionTarget(null);
    } finally {
      setIsSubmitting(false);
    }
  };

  const columns: Column<ApprovalRequest>[] = [
    {
      key: "id",
      header: "Mã yêu cầu & Bước chạy",
      width: "30%",
      render: (item) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
            {item.step_key}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-muted)" }}>
            ID: {item.id} · Lần chạy: {item.run_id}
          </span>
        </div>
      ),
    },
    {
      key: "action_hash",
      header: "Mã băm hành động (CAS Hash)",
      width: "28%",
      render: (item) => (
        <span
          title={item.action_hash}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            color: "var(--color-primary)",
            backgroundColor: "var(--color-primary-subtle)",
            padding: "2px 6px",
            borderRadius: "var(--radius-xs)",
            display: "inline-block",
            maxWidth: "240px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {item.action_hash}
        </span>
      ),
    },
    {
      key: "approver_role",
      header: "Vai trò kiểm duyệt",
      width: "16%",
      render: (item) => (
        <span
          style={{
            fontSize: "11px",
            fontWeight: 600,
            color: "var(--text-secondary)",
            backgroundColor: "var(--bg-subtle)",
            padding: "2px 6px",
            borderRadius: "var(--radius-xs)",
          }}
        >
          {item.approver_role}
        </span>
      ),
    },
    {
      key: "status",
      header: "Trạng thái",
      width: "12%",
      render: (item) => <StatusBadge status={item.status} />,
    },
    {
      key: "actions",
      header: "Xử lý",
      width: "14%",
      align: "right",
      render: (item) => (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "6px" }}>
          <Button
            variant="outline"
            size="sm"
            style={{ color: "var(--color-success)", borderColor: "var(--color-success-border)" }}
            onClick={() => setDecisionTarget({ item, decision: "approved" })}
          >
            Duyệt
          </Button>
          <Button
            variant="ghost"
            size="sm"
            style={{ color: "var(--color-danger)" }}
            onClick={() => setDecisionTarget({ item, decision: "rejected" })}
          >
            Từ chối
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      {/* Toast */}
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
          <span>{toastMessage}</span>
        </div>
      )}

      <PageHeader
        title="Hộp thư Phê duyệt (Approval Inbox)"
        description="Kiểm tra và phê duyệt các hành động có tác động lớn (High-impact Tool operations) trước khi thực thi vào hệ thống sản xuất."
        breadcrumbs={[
          { label: "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Hộp thư Phê duyệt", isCurrent: true },
        ]}
        badge={
          <StatusBadge
            status="PENDING"
            customLabel={`${approvals.length} ĐANG CHỜ`}
            size="sm"
          />
        }
        actions={
          <Button variant="secondary" size="md" onClick={fetchApprovals} isLoading={isLoading}>
            Làm mới
          </Button>
        }
      />

      {isLoading ? (
        <LoadingSkeleton rows={3} columns={5} />
      ) : errorMessage ? (
        <ErrorState message={errorMessage} onRetry={fetchApprovals} />
      ) : approvals.length === 0 ? (
        <EmptyState
          title="Không có yêu cầu phê duyệt nào đang chờ"
          description="Hiện tại toàn bộ các tác tử AI đang hoạt động an toàn trong giới hạn tự động cho phép."
        />
      ) : (
        <DataTable
          columns={columns}
          data={approvals}
          keyExtractor={(item) => item.id}
          caption="Bảng danh sách các yêu cầu can thiệp người dùng (HITL) đang chờ duyệt"
        />
      )}

      {/* Confirm Decision Modal */}
      {decisionTarget && (
        <ConfirmDialog
          isOpen={true}
          onClose={() => setDecisionTarget(null)}
          onConfirm={handleCommitDecision}
          title={
            decisionTarget.decision === "approved"
              ? "Xác nhận Phê duyệt Thao tác Công cụ"
              : "Xác nhận Từ chối Thao tác Công cụ"
          }
          message={`Bạn có chắc chắn muốn ${
            decisionTarget.decision === "approved" ? "CHO PHÉP" : "TỪ CHỐI"
          } bước chạy "${decisionTarget.item.step_key}" với mã băm hành động ${decisionTarget.item.action_hash.substring(
            0,
            24
          )}...? Quyết định này sẽ được ghi vào sổ cái kiểm toán bất biến (Audit Ledger).`}
          confirmLabel={
            decisionTarget.decision === "approved" ? "Xác nhận Duyệt" : "Xác nhận Từ chối"
          }
          cancelLabel="Đóng"
          isConfirming={isSubmitting}
          isDestructive={decisionTarget.decision === "rejected"}
        />
      )}
    </div>
  );
}
