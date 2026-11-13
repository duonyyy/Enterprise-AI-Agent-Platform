"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ADMIN_MANAGEABLE_WORKSPACE_ROLES,
  ApiClientError,
  MembershipStatus,
  WORKSPACE_ROLES,
  WorkspaceMembershipDTO,
  WorkspaceRole,
  canActorManageTargetRole,
  defaultApiClient,
} from "@enterprise/api-client";
import {
  Button,
  Column,
  DataTable,
  EmptyState,
  ErrorState,
  ForbiddenState,
  Input,
  LoadingSkeleton,
  Modal,
  PageHeader,
  StatusBadge,
} from "@enterprise/ui";
import { useWorkspaceSession } from "../workspace-context";

type StatusFilterOption = "all" | MembershipStatus;

type PendingConfirmation =
  | {
      kind: "role_change";
      member: WorkspaceMembershipDTO;
      nextRole: WorkspaceRole;
    }
  | {
      kind: "revoke";
      member: WorkspaceMembershipDTO;
    };

const ROLE_DISPLAY_NAMES: Record<WorkspaceRole, string> = {
  owner: "Chủ sở hữu (Owner)",
  admin: "Quản trị viên (Admin)",
  developer: "Kỹ sư phát triển (Developer)",
  operator: "Vận hành (Operator)",
  viewer: "Người xem (Viewer)",
};

function formatJoinedAt(iso: string): string {
  try {
    return new Intl.DateTimeFormat("vi-VN", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export default function SettingsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const workspaceId = params.workspaceId;
  const {
    currentUser,
    currentWorkspace,
    actorRole,
    requestSignal,
    refreshWorkspaceAuth,
  } = useWorkspaceSession();

  const canManageMembers = actorRole === "owner" || actorRole === "admin";

  const [statusFilter, setStatusFilter] = useState<StatusFilterOption>("all");
  const [members, setMembers] = useState<WorkspaceMembershipDTO[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [pendingAction, setPendingAction] = useState<PendingConfirmation | null>(null);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [bannerNotice, setBannerNotice] = useState<string | null>(null);
  const [serviceUnavailable, setServiceUnavailable] = useState<boolean>(false);
  const [forbiddenView, setForbiddenView] = useState<boolean>(false);

  const listGenerationRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);

  const loadMembers = useCallback(
    async (options?: { cursor?: string | null; append?: boolean }) => {
      if (!canManageMembers) {
        return;
      }

      const append = Boolean(options?.append);
      const generation = ++listGenerationRef.current;

      if (!append) {
        activeControllerRef.current?.abort();
      }
      const controller = new AbortController();
      activeControllerRef.current = controller;

      const onParentAbort = () => controller.abort();
      if (requestSignal.aborted) {
        controller.abort();
        return;
      }
      requestSignal.addEventListener("abort", onParentAbort, { once: true });

      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoading(true);
        setServiceUnavailable(false);
        setForbiddenView(false);
      }

      try {
        const res = await defaultApiClient.listWorkspaceMemberships(workspaceId, {
          limit: 25,
          cursor: options?.cursor ?? undefined,
          status: statusFilter === "all" ? undefined : statusFilter,
          signal: controller.signal,
        });

        if (generation !== listGenerationRef.current || requestSignal.aborted || controller.signal.aborted) {
          return;
        }

        setMembers((prev) => (append ? [...prev, ...res.items] : res.items));
        setNextCursor(res.next_cursor ?? null);
      } catch (err: unknown) {
        if (generation !== listGenerationRef.current || requestSignal.aborted || controller.signal.aborted) {
          return;
        }
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiClientError) {
          if (err.status === 403) {
            setForbiddenView(true);
            setMembers([]);
            void refreshWorkspaceAuth();
            return;
          }
          if (err.status === 401) {
            void refreshWorkspaceAuth();
            return;
          }
          setBannerError(err.errorMessage || "Không thể tải danh sách thành viên.");
        }
        setServiceUnavailable(true);
      } finally {
        requestSignal.removeEventListener("abort", onParentAbort);
        if (generation === listGenerationRef.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [canManageMembers, refreshWorkspaceAuth, requestSignal, statusFilter, workspaceId]
  );

  useEffect(() => {
    setMembers([]);
    setNextCursor(null);
    setBannerError(null);
    setBannerNotice(null);
    setPendingAction(null);
    setForbiddenView(false);
    setServiceUnavailable(false);

    if (canManageMembers) {
      void loadMembers({ append: false });
    }

    return () => {
      listGenerationRef.current += 1;
      activeControllerRef.current?.abort();
    };
  }, [canManageMembers, loadMembers, workspaceId]);

  const activeOwnerCount = useMemo(
    () => members.filter((m) => m.status === "active" && m.role === "owner").length,
    [members]
  );

  const selectableRolesForActor: readonly WorkspaceRole[] = useMemo(() => {
    if (actorRole === "owner") {
      return WORKSPACE_ROLES;
    }
    if (actorRole === "admin") {
      return ADMIN_MANAGEABLE_WORKSPACE_ROLES;
    }
    return [];
  }, [actorRole]);

  const executeConfirmedAction = async () => {
    if (!pendingAction || isSubmitting) {
      return;
    }
    const action = pendingAction;
    setIsSubmitting(true);
    setBannerError(null);
    setBannerNotice(null);

    try {
      if (action.kind === "role_change") {
        const res = await defaultApiClient.updateWorkspaceMembershipRole(
          workspaceId,
          action.member.membership_id,
          {
            role: action.nextRole,
            expected_role: action.member.role,
            expected_status: "active",
          },
          requestSignal
        );
        setPendingAction(null);
        if (res.outcome === "no_op") {
          setBannerNotice("Vai trò của thành viên không thay đổi.");
        } else {
          setBannerNotice(
            `Đã cập nhật vai trò thành viên ${res.display_name || res.user_id} thành ${ROLE_DISPLAY_NAMES[res.role]}.`
          );
        }
        await loadMembers({ append: false });
        if (action.member.user_id === currentUser.user_id) {
          await refreshWorkspaceAuth();
        }
      } else {
        const res = await defaultApiClient.revokeWorkspaceMembership(
          workspaceId,
          action.member.membership_id,
          {
            expected_role: action.member.role,
            expected_status: "active",
          },
          requestSignal
        );
        setPendingAction(null);
        setBannerNotice(
          res.outcome === "already_revoked"
            ? "Thành viên đã ở trạng thái thu hồi quyền trước đó."
            : `Đã thu hồi quyền truy cập của ${res.display_name || res.user_id} khỏi workspace.`
        );
        if (action.member.user_id === currentUser.user_id) {
          await refreshWorkspaceAuth();
        } else {
          await loadMembers({ append: false });
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setPendingAction(null);
        return;
      }
      if (err instanceof ApiClientError) {
        if (err.status === 409) {
          setPendingAction(null);
          setBannerError(
            err.errorMessage ||
              "Trạng thái thành viên đã thay đổi hoặc đây là Owner hoạt động cuối cùng. Danh sách đã được tải lại."
          );
          await loadMembers({ append: false });
          return;
        }
        if (err.status === 403 || err.status === 401) {
          setPendingAction(null);
          setBannerError(
            err.errorMessage || "Bạn không còn đủ quyền thực hiện thao tác này."
          );
          await refreshWorkspaceAuth();
          return;
        }
        if (err.status === 404) {
          setPendingAction(null);
          setBannerError("Không tìm thấy thành viên trong workspace hiện tại.");
          await loadMembers({ append: false });
          return;
        }
        setBannerError(err.errorMessage || "Không thể hoàn tất thao tác quản trị thành viên.");
      } else {
        setBannerError("Máy chủ tạm thời gián đoạn. Vui lòng thử lại sau.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const columns: Column<WorkspaceMembershipDTO>[] = [
    {
      key: "member",
      header: "Thành viên",
      render: (m) => {
        const isSelf = m.user_id === currentUser.user_id;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {m.display_name || m.user_id}
              {isSelf ? " (Bạn)" : ""}
            </span>
            <span style={{ fontSize: "11px", color: "var(--text-muted)", fontFamily: "monospace" }}>
              {m.user_id}
            </span>
          </div>
        );
      },
    },
    {
      key: "role",
      header: "Vai trò",
      render: (m) => {
        const isManageableActive =
          m.status === "active" && canActorManageTargetRole(actorRole, m.role);
        const isLastKnownActiveOwner =
          statusFilter !== "revoked" &&
          statusFilter !== "invited" &&
          m.status === "active" &&
          m.role === "owner" &&
          activeOwnerCount <= 1;

        if (!isManageableActive) {
          return <StatusBadge status="PUBLISHED" customLabel={ROLE_DISPLAY_NAMES[m.role]} size="sm" />;
        }

        return (
          <select
            aria-label={`Vai trò của ${m.display_name || m.user_id}`}
            value={m.role}
            disabled={isSubmitting || isLastKnownActiveOwner}
            title={
              isLastKnownActiveOwner
                ? "Không thể hạ quyền Chủ sở hữu hoạt động cuối cùng"
                : "Chọn vai trò mới"
            }
            onChange={(e) => {
              const nextRole = e.target.value as WorkspaceRole;
              if (nextRole !== m.role) {
                setBannerError(null);
                setBannerNotice(null);
                setPendingAction({ kind: "role_change", member: m, nextRole });
              }
            }}
            style={{
              padding: "6px 10px",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-strong)",
              backgroundColor: "var(--bg-surface)",
              color: "var(--text-primary)",
              fontSize: "12px",
            }}
          >
            {selectableRolesForActor.map((r) => (
              <option key={r} value={r}>
                {ROLE_DISPLAY_NAMES[r]}
              </option>
            ))}
          </select>
        );
      },
    },
    {
      key: "status",
      header: "Trạng thái",
      render: (m) => {
        if (m.status === "active") {
          return <StatusBadge status="SUCCEEDED" customLabel="Hoạt động" size="sm" />;
        }
        if (m.status === "invited") {
          return <StatusBadge status="WAITING" customLabel="Đang mời" size="sm" />;
        }
        return <StatusBadge status="FAILED" customLabel="Đã thu hồi" size="sm" />;
      },
    },
    {
      key: "joined_at",
      header: "Tham gia lúc",
      render: (m) => (
        <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
          {formatJoinedAt(m.joined_at)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "Thao tác",
      align: "right",
      render: (m) => {
        if (m.status !== "active" || !canActorManageTargetRole(actorRole, m.role)) {
          return (
            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
              Không khả dụng
            </span>
          );
        }
        const isLastKnownActiveOwner =
          statusFilter !== "revoked" &&
          statusFilter !== "invited" &&
          m.role === "owner" &&
          activeOwnerCount <= 1;

        return (
          <Button
            variant="danger"
            size="sm"
            disabled={isSubmitting || isLastKnownActiveOwner}
            onClick={() => {
              setBannerError(null);
              setBannerNotice(null);
              setPendingAction({ kind: "revoke", member: m });
            }}
          >
            Thu hồi quyền
          </Button>
        );
      },
    },
  ];

  return (
    <div style={{ maxWidth: "1020px" }}>
      <PageHeader
        title="Cài đặt Không gian Làm việc"
        description="Thông tin định danh workspace thực tế và quản trị phân quyền thành viên theo ranh giới workspace."
        breadcrumbs={[
          { label: currentWorkspace.workspace_name || "Không gian làm việc", href: `/w/${workspaceId}/overview` },
          { label: "Cài đặt", isCurrent: true },
        ]}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
        {/* Section 1: Authoritative Workspace & Tenant Metadata */}
        <div
          style={{
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
            <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
              Thông tin Không gian Làm việc
            </h2>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <StatusBadge status="SUCCEEDED" customLabel="Hoạt động" size="sm" />
              <StatusBadge
                status="PUBLISHED"
                customLabel={ROLE_DISPLAY_NAMES[actorRole as WorkspaceRole] || actorRole}
                size="sm"
              />
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "14px" }}>
            <Input
              label="Mã định danh Không gian (Workspace ID)"
              value={currentWorkspace.workspace_id}
              disabled
            />
            <Input
              label="Tên Không gian Làm việc"
              value={`${currentWorkspace.workspace_name} (${currentWorkspace.workspace_slug})`}
              disabled
            />
            <Input
              label="Tổ chức / Tenant Chủ quản"
              value={`${currentWorkspace.tenant_name} (${currentWorkspace.tenant_slug})`}
              disabled
            />
          </div>
        </div>

        {/* Section 2: Member Management */}
        <div
          style={{
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            padding: "20px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
            }}
          >
            <div>
              <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
                Thành viên & Phân quyền Workspace
              </h2>
              <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: "4px 0 0 0" }}>
                Mọi thay đổi vai trò hoặc thu hồi quyền có hiệu lực tức thời với Control API và các luồng thực thi.
              </p>
            </div>

            {canManageMembers && !forbiddenView && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {(
                  [
                    { key: "all", label: "Tất cả" },
                    { key: "active", label: "Hoạt động" },
                    { key: "invited", label: "Đang mời" },
                    { key: "revoked", label: "Đã thu hồi" },
                  ] as const
                ).map((item) => (
                  <Button
                    key={item.key}
                    variant={statusFilter === item.key ? "primary" : "secondary"}
                    size="sm"
                    disabled={isLoading}
                    onClick={() => setStatusFilter(item.key)}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
            )}
          </div>

          {bannerError && (
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid rgba(239, 68, 68, 0.35)",
                backgroundColor: "rgba(239, 68, 68, 0.08)",
                color: "var(--color-danger, #dc2626)",
                fontSize: "13px",
              }}
            >
              {bannerError}
            </div>
          )}

          {bannerNotice && (
            <div
              role="status"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid rgba(16, 185, 129, 0.35)",
                backgroundColor: "rgba(16, 185, 129, 0.08)",
                color: "var(--color-success, #047857)",
                fontSize: "13px",
              }}
            >
              {bannerNotice}
            </div>
          )}

          {!canManageMembers || forbiddenView ? (
            <ForbiddenState
              title="Chỉ Owner hoặc Admin mới có quyền quản lý thành viên"
              message="Vai trò hiện tại của bạn trong workspace này không được phép xem danh sách quản trị hoặc thay đổi vai trò thành viên."
            />
          ) : serviceUnavailable ? (
            <ErrorState
              title="Không thể tải danh sách thành viên"
              message="Dịch vụ kiểm tra quyền hoặc cơ sở dữ liệu tạm thời gián đoạn."
              onRetry={() => void loadMembers({ append: false })}
            />
          ) : isLoading ? (
            <LoadingSkeleton rows={4} />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={members}
                keyExtractor={(item) => item.membership_id}
                caption="Danh sách thành viên trong workspace hiện tại"
                emptyState={
                  <EmptyState
                    title="Không có thành viên phù hợp bộ lọc"
                    description="Chưa có bản ghi thành viên nào khớp trạng thái đang chọn trong workspace này."
                  />
                }
              />

              {nextCursor && (
                <div style={{ display: "flex", justifyContent: "center" }}>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={isLoadingMore}
                    onClick={() => void loadMembers({ cursor: nextCursor, append: true })}
                  >
                    {isLoadingMore ? "Đang tải thêm..." : "Tải thêm thành viên"}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Explicit Confirmation Modal for Member Role Update or Revoke */}
      <Modal
        isOpen={pendingAction !== null}
        onClose={() => {
          if (!isSubmitting) {
            setPendingAction(null);
          }
        }}
        title={
          pendingAction?.kind === "role_change"
            ? "Xác nhận thay đổi vai trò thành viên"
            : "Xác nhận thu hồi quyền thành viên"
        }
        description={
          pendingAction?.kind === "role_change"
            ? "Thao tác này áp dụng nguyên tử dưới khóa workspace và ghi nhật ký kiểm toán."
            : "Thành viên bị thu hồi sẽ mất quyền truy cập vào workspace này ngay lập tức mà không làm đăng xuất các workspace khác."
        }
        footer={
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
            <Button
              variant="secondary"
              size="md"
              disabled={isSubmitting}
              onClick={() => setPendingAction(null)}
            >
              Hủy bỏ
            </Button>
            <Button
              variant={pendingAction?.kind === "revoke" ? "danger" : "primary"}
              size="md"
              disabled={isSubmitting}
              onClick={() => void executeConfirmedAction()}
            >
              {isSubmitting ? "Đang xử lý..." : "Xác nhận thực hiện"}
            </Button>
          </div>
        }
      >
        {pendingAction && (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", fontSize: "13px" }}>
            <div>
              <strong>Thành viên:</strong>{" "}
              {pendingAction.member.display_name || pendingAction.member.user_id}
            </div>
            <div>
              <strong>Vai trò hiện tại:</strong> {ROLE_DISPLAY_NAMES[pendingAction.member.role]}
            </div>
            {pendingAction.kind === "role_change" ? (
              <div>
                <strong>Vai trò mới:</strong> {ROLE_DISPLAY_NAMES[pendingAction.nextRole]}
              </div>
            ) : (
              <div style={{ color: "var(--color-danger, #dc2626)" }}>
                Trạng thái mới sau thao tác: <strong>Đã thu hồi (revoked)</strong>
              </div>
            )}
            {pendingAction.kind === "role_change" &&
              pendingAction.nextRole === "owner" && (
                <p style={{ margin: 0, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                  Lưu ý: Thành viên được nâng lên Chủ sở hữu (Owner) sẽ có toàn quyền quản trị các Owner/Admin khác và mời/thu hồi thành viên trong workspace này.
                </p>
              )}
            {pendingAction.member.user_id === currentUser.user_id && (
              <p style={{ margin: 0, color: "var(--color-warning, #b45309)", lineHeight: 1.5 }}>
                Cảnh báo: Bạn đang thao tác trên chính tư cách thành viên của mình trong workspace hiện tại.
              </p>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
