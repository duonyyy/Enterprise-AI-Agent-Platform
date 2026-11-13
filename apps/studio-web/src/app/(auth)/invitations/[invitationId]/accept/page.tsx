"use client";

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { BrandIcon, Button, StatusBadge } from "@enterprise/ui";
import {
  ApiClientError,
  defaultApiClient,
  InvitationPreviewResponseDTO,
} from "@enterprise/api-client";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ROLE_LABELS: Record<string, string> = {
  owner: "Chủ sở hữu (Owner)",
  admin: "Quản trị viên (Admin)",
  developer: "Kỹ sư phát triển (Developer)",
  operator: "Vận hành (Operator)",
  viewer: "Người xem (Viewer)",
};

type InvitationUiState =
  | "storing"
  | "verifying"
  | "login"
  | "confirm"
  | "submitting"
  | "success"
  | "already_joined"
  | "mismatch"
  | "unverified"
  | "stale"
  | "invalid"
  | "expired"
  | "revoked"
  | "conflict"
  | "rate_limited"
  | "unavailable";

function extractFragmentToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const rawHash = window.location.hash || "";
  if (!rawHash || rawHash.length <= 1) {
    return null;
  }
  const fragment = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
  const params = new URLSearchParams(fragment);
  const token = params.get("token");
  return token && token.trim().length > 0 ? token.trim() : null;
}

function scrubUrlFragment(): void {
  if (typeof window === "undefined") {
    return;
  }
  if (window.location.hash) {
    const cleanUrl = `${window.location.pathname}${window.location.search}`;
    window.history.replaceState(null, "", cleanUrl);
  }
}

function resolveSafeWorkspaceRedirect(
  redirectUrl: string | null | undefined,
  workspaceId: string
): string {
  const prefix = `/w/${workspaceId}/`;
  if (
    redirectUrl &&
    redirectUrl.startsWith(prefix) &&
    !redirectUrl.startsWith("//") &&
    !redirectUrl.includes("\\") &&
    !redirectUrl.includes("#")
  ) {
    return redirectUrl;
  }
  return `/w/${workspaceId}/agents`;
}

function formatExpiry(iso: string): string {
  try {
    return new Intl.DateTimeFormat("vi-VN", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function InvitationAcceptanceContent() {
  const params = useParams<{ invitationId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const invitationId = typeof params?.invitationId === "string" ? params.invitationId : "";

  const [uiState, setUiState] = useState<InvitationUiState>("storing");
  const [preview, setPreview] = useState<InvitationPreviewResponseDTO | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isActionBusy, setIsActionBusy] = useState(false);
  const [failedStep, setFailedStep] = useState<"handoff" | "preview" | "accept">("handoff");
  const [canSwitchAccount, setCanSwitchAccount] = useState(false);
  const bootStartedRef = useRef(false);
  const pendingHandoffTokenRef = useRef<string | null>(null);

  const loadPreview = useCallback(async () => {
    setUiState("verifying");
    setErrorMessage(null);
    setCanSwitchAccount(false);
    try {
      const data = await defaultApiClient.previewInvitation(invitationId);
      setPreview(data);
      if (data.acceptance_state === "already_accepted" || !data.can_accept) {
        setUiState("already_joined");
      } else {
        setUiState("confirm");
      }
    } catch (err: unknown) {
      if (!(err instanceof ApiClientError)) {
        setFailedStep("preview");
        setUiState("unavailable");
        setErrorMessage("Không thể kết nối tới máy chủ để kiểm tra lời mời.");
        return;
      }

      const code = err.errorCode;
      if (err.status === 401) {
        setUiState("login");
        return;
      }
      if (err.status === 403) {
        if (code === "reauthentication_required") {
          setUiState("stale");
          return;
        }
        if (code === "recipient_email_unverified") {
          setUiState("unverified");
          return;
        }
        setCanSwitchAccount(true);
        setUiState("mismatch");
        return;
      }
      if (err.status === 404) {
        setCanSwitchAccount(true);
        setUiState("invalid");
        return;
      }
      if (err.status === 410) {
        setUiState(code === "invitation_revoked" ? "revoked" : "expired");
        return;
      }
      if (err.status === 409) {
        setUiState("conflict");
        setErrorMessage(err.errorMessage || "Lời mời không thể hoàn tất do xung đột trạng thái.");
        return;
      }
      if (err.status === 429) {
        setFailedStep("preview");
        setUiState("rate_limited");
        return;
      }
      setFailedStep("preview");
      setUiState("unavailable");
      setErrorMessage(err.errorMessage || "Hệ thống tạm thời gián đoạn (503).");
    }
  }, [invitationId]);

  const runBootstrap = useCallback(async () => {
    if (!UUID_RE.test(invitationId)) {
      pendingHandoffTokenRef.current = null;
      scrubUrlFragment();
      setCanSwitchAccount(false);
      setUiState("invalid");
      return;
    }

    const queryErr = searchParams?.get("error");
    if (queryErr === "handoff_binding_mismatch") {
      pendingHandoffTokenRef.current = null;
      scrubUrlFragment();
      setCanSwitchAccount(false);
      setUiState("invalid");
      setErrorMessage(
        "Phiên nhận lời mời không khớp với trình duyệt đã khởi tạo. Vui lòng mở lại liên kết từ email."
      );
      return;
    }

    const fragmentToken = pendingHandoffTokenRef.current ?? extractFragmentToken();
    if (fragmentToken) {
      pendingHandoffTokenRef.current = fragmentToken;
      setUiState("storing");
      setErrorMessage(null);
      try {
        await defaultApiClient.createInvitationHandoff(invitationId, {
          token: fragmentToken,
        });
        pendingHandoffTokenRef.current = null;
        scrubUrlFragment();
      } catch (err: unknown) {
        setFailedStep("handoff");
        if (err instanceof ApiClientError) {
          if (err.status === 429) {
            setUiState("rate_limited");
            return;
          }
          if (err.status === 409) {
            pendingHandoffTokenRef.current = null;
            scrubUrlFragment();
            setUiState("conflict");
            setErrorMessage(
              err.errorMessage || "Lời mời không còn hiệu lực hoặc phiên nhận trước đã hoàn tất."
            );
            return;
          }
          if (err.status === 403 || err.status === 422 || err.status === 404) {
            pendingHandoffTokenRef.current = null;
            scrubUrlFragment();
            setCanSwitchAccount(false);
            setUiState("invalid");
            return;
          }
        }
        setUiState("unavailable");
        setErrorMessage("Không thể lưu phiên bảo mật cho lời mời (503). Vui lòng thử lại.");
        return;
      }
    } else {
      scrubUrlFragment();
    }

    await loadPreview();
  }, [invitationId, loadPreview, searchParams]);

  useEffect(() => {
    if (bootStartedRef.current) {
      return;
    }
    bootStartedRef.current = true;
    void runBootstrap();
  }, [runBootstrap]);

  // Cross-tab logout listener keeps invite UI fail-closed if another tab logs out
  useEffect(() => {
    let channel: BroadcastChannel | null = null;
    try {
      if (typeof window !== "undefined" && "BroadcastChannel" in window) {
        channel = new BroadcastChannel("enterprise_auth_channel");
        channel.onmessage = (event) => {
          if (event.data === "LOGOUT") {
            setPreview(null);
            setUiState("login");
          }
        };
      }
    } catch {
      // Ignore broadcast channel errors
    }
    return () => {
      channel?.close();
    };
  }, []);

  const handleOidcStart = async (forceReauth: boolean) => {
    if (isActionBusy) {
      return;
    }
    setIsActionBusy(true);
    try {
      const returnPath = `/invitations/${encodeURIComponent(invitationId)}/accept`;
      const { authorization_url } = await defaultApiClient.getLoginUrl(returnPath, {
        invitationId,
        reauth: forceReauth,
      });
      if (authorization_url) {
        window.location.href = authorization_url;
        return;
      }
      const fallbackParams = new URLSearchParams({
        redirect: "true",
        invitation_id: invitationId,
        return_to: returnPath,
      });
      if (forceReauth) {
        fallbackParams.set("reauth", "true");
      }
      window.location.href = `/v1/auth/login?${fallbackParams.toString()}`;
    } catch {
      setIsActionBusy(false);
      setUiState("unavailable");
      setErrorMessage("Không thể khởi tạo phiên đăng nhập OIDC.");
    }
  };

  const handleSwitchAccount = async () => {
    if (isActionBusy) {
      return;
    }
    setIsActionBusy(true);
    try {
      await defaultApiClient.logout();
    } catch {
      // Best-effort clear; user will still start fresh reauth flow if needed
    }
    pendingHandoffTokenRef.current = null;
    scrubUrlFragment();
    setCanSwitchAccount(false);
    setIsActionBusy(false);
    setUiState("invalid");
    setErrorMessage(
      "Đã đăng xuất khỏi tài khoản cũ. Vui lòng mở lại liên kết lời mời từ email để xác nhận đúng tài khoản."
    );
  };

  const submitAccept = async () => {
    if (isActionBusy) {
      return;
    }
    setIsActionBusy(true);
    setUiState("submitting");
    setErrorMessage(null);
    try {
      const result = await defaultApiClient.acceptInvitation(invitationId);
      // Re-read authoritative memberships via /v1/auth/me before entering workspace shell
      const refreshedUser = await defaultApiClient.getCurrentUser();
      const targetUrl = resolveSafeWorkspaceRedirect(result.redirect_url, result.workspace_id);
      const hasMembership = refreshedUser.workspaces.some(
        (w) => w.workspace_id === result.workspace_id
      );
      setUiState("success");
      if (hasMembership) {
        router.replace(targetUrl);
      } else {
        setIsActionBusy(false);
      }
    } catch (err: unknown) {
      setIsActionBusy(false);
      if (!(err instanceof ApiClientError)) {
        setFailedStep("accept");
        setUiState("unavailable");
        setErrorMessage("Mất kết nối khi xác nhận lời mời. Bạn có thể thử lại an toàn.");
        return;
      }
      const code = err.errorCode;
      if (err.status === 401) {
        setUiState("login");
      } else if (err.status === 403) {
        if (code === "reauthentication_required") {
          setUiState("stale");
        } else if (code === "recipient_email_unverified") {
          setUiState("unverified");
        } else {
          setCanSwitchAccount(true);
          setUiState("mismatch");
        }
      } else if (err.status === 404) {
        setCanSwitchAccount(true);
        setUiState("invalid");
      } else if (err.status === 410) {
        setUiState(code === "invitation_revoked" ? "revoked" : "expired");
      } else if (err.status === 409) {
        setUiState("conflict");
        setErrorMessage(err.errorMessage || "Lời mời không còn hiệu lực hoặc bạn đã có quyền trong workspace.");
      } else {
        setFailedStep("accept");
        setUiState(err.status === 429 ? "rate_limited" : "unavailable");
        setErrorMessage(err.errorMessage || "Hệ thống tạm thời gián đoạn (503).");
      }
    }
  };

  const handleConfirmAccept = async () => {
    if (uiState !== "confirm") {
      return;
    }
    await submitAccept();
  };

  const handleRetryFailedStep = async () => {
    if (isActionBusy) {
      return;
    }
    if (failedStep === "handoff" || pendingHandoffTokenRef.current !== null) {
      await runBootstrap();
      return;
    }
    if (failedStep === "accept") {
      await submitAccept();
      return;
    }
    await loadPreview();
  };

  return (
    <main
      id="main-content"
      data-ui-state={uiState}
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "var(--bg-canvas)",
        padding: "24px",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "480px",
          backgroundColor: "var(--bg-surface)",
          border: "1px solid var(--border-strong)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-lg)",
          padding: "32px",
          display: "flex",
          flexDirection: "column",
          gap: "20px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div
            style={{
              width: "40px",
              height: "40px",
              borderRadius: "10px",
              backgroundColor: "var(--color-primary-subtle)",
              border: "1px solid var(--color-primary-border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <BrandIcon name="safety-shield" size={20} color="#0f766e" />
          </div>
          <div>
            <h1 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
              Lời mời tham gia Workspace
            </h1>
            <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "2px 0 0 0" }}>
              Enterprise Agent Studio
            </p>
          </div>
        </div>

        {(uiState === "storing" || uiState === "verifying" || uiState === "submitting") && (
          <div
            role="status"
            style={{
              padding: "16px",
              borderRadius: "var(--radius-md)",
              backgroundColor: "var(--bg-muted)",
              fontSize: "13px",
              color: "var(--text-secondary)",
            }}
          >
            {uiState === "storing" && "Đang bảo mật thông tin lời mời trên máy chủ..."}
            {uiState === "verifying" && "Đang xác minh danh tính và kiểm tra lời mời..."}
            {uiState === "submitting" && "Đang kích hoạt quyền thành viên workspace..."}
          </div>
        )}

        {uiState === "login" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
              Liên kết lời mời đã được bảo mật. Vui lòng đăng nhập bằng tài khoản Identity Provider được mời để xem chi tiết và xác nhận tham gia workspace.
            </p>
            <Button
              variant="primary"
              size="lg"
              disabled={isActionBusy}
              onClick={() => handleOidcStart(false)}
              style={{ width: "100%" }}
            >
              Đăng nhập bằng OIDC để tiếp tục
            </Button>
          </div>
        )}

        {uiState === "stale" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "rgba(245, 158, 11, 0.1)",
                border: "1px solid rgba(245, 158, 11, 0.35)",
                color: "#b45309",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              Phiên xác thực email đã quá 15 phút hoặc chưa có bằng chứng danh tính mới. Vui lòng xác thực lại qua OIDC trước khi xem và chấp nhận lời mời.
            </div>
            <Button
              variant="primary"
              size="lg"
              disabled={isActionBusy}
              onClick={() => handleOidcStart(true)}
              style={{ width: "100%" }}
            >
              Xác thực lại với OIDC
            </Button>
          </div>
        )}

        {uiState === "unverified" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                color: "#b91c1c",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              Email trên tài khoản Identity Provider hiện tại chưa được xác minh (`email_verified = true`). Vui lòng hoàn tất xác minh email tại IdP rồi thử kiểm tra lại.
            </div>
            <Button variant="secondary" size="md" onClick={() => void loadPreview()}>
              Kiểm tra lại
            </Button>
          </div>
        )}

        {uiState === "mismatch" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                color: "#b91c1c",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              Tài khoản đang đăng nhập không khớp với người nhận của lời mời này. Vui lòng đăng xuất và đăng nhập bằng đúng tài khoản được mời.
            </div>
            <Button
              variant="secondary"
              size="md"
              disabled={isActionBusy}
              onClick={() => void handleSwitchAccount()}
            >
              Đăng xuất tài khoản hiện tại
            </Button>
          </div>
        )}

        {uiState === "confirm" && preview && (
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <div
              style={{
                padding: "16px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "var(--bg-muted)",
                border: "1px solid var(--border-subtle)",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Tổ chức</span>
                <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)" }}>
                  {preview.tenant_name}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Workspace</span>
                <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)" }}>
                  {preview.workspace_name} ({preview.workspace_slug})
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Vai trò được cấp</span>
                <StatusBadge status="PUBLISHED" customLabel={ROLE_LABELS[preview.role] || preview.role} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Hạn lời mời</span>
                <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                  {formatExpiry(preview.expires_at)}
                </span>
              </div>
            </div>

            <Button
              variant="primary"
              size="lg"
              disabled={isActionBusy}
              onClick={() => void handleConfirmAccept()}
              style={{ width: "100%" }}
            >
              Xác nhận và tham gia Workspace
            </Button>
          </div>
        )}

        {(uiState === "already_joined" || uiState === "success") && preview && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              role="status"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "rgba(16, 185, 129, 0.1)",
                border: "1px solid rgba(16, 185, 129, 0.3)",
                color: "#047857",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              {uiState === "success"
                ? `Đã kích hoạt quyền thành viên tại workspace ${preview.workspace_name}. Đang chuyển hướng...`
                : `Bạn đã tham gia workspace ${preview.workspace_name} với vai trò ${ROLE_LABELS[preview.role] || preview.role}.`}
            </div>
            <Button
              variant="primary"
              size="lg"
              onClick={() => router.replace(`/w/${preview.workspace_id}/agents`)}
              style={{ width: "100%" }}
            >
              Mở Workspace
            </Button>
          </div>
        )}

        {(uiState === "invalid" ||
          uiState === "expired" ||
          uiState === "revoked" ||
          uiState === "conflict" ||
          uiState === "rate_limited" ||
          uiState === "unavailable") && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                color: "#b91c1c",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              {errorMessage ||
                (uiState === "expired"
                  ? "Lời mời này đã hết hạn sử dụng. Vui lòng liên hệ quản trị viên workspace để gửi lại lời mời."
                  : uiState === "revoked"
                    ? "Lời mời này đã bị thu hồi và không thể sử dụng."
                    : uiState === "rate_limited"
                      ? "Quá nhiều yêu cầu xác nhận lời mời trong thời gian ngắn. Vui lòng thử lại sau."
                      : uiState === "unavailable"
                        ? "Hệ thống tạm thời gián đoạn (503). Vui lòng thử lại sau."
                        : "Liên kết lời mời không hợp lệ hoặc phiên 10 phút đã hết hạn. Vui lòng mở lại liên kết từ email.")}
            </div>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
              {uiState === "unavailable" && (
                <Button
                  variant="primary"
                  size="md"
                  disabled={isActionBusy}
                  onClick={() => void handleRetryFailedStep()}
                >
                  Thử lại
                </Button>
              )}
              {uiState === "invalid" && canSwitchAccount && (
                <Button
                  variant="secondary"
                  size="md"
                  disabled={isActionBusy}
                  onClick={() => void handleSwitchAccount()}
                >
                  Đăng xuất tài khoản hiện tại
                </Button>
              )}
              <Button variant="secondary" size="md" onClick={() => router.replace("/login")}>
                Về trang đăng nhập
              </Button>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

export default function InvitationAcceptPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "var(--bg-canvas)",
            color: "var(--text-secondary)",
            fontSize: "13px",
          }}
        >
          Đang tải trang nhận lời mời...
        </div>
      }
    >
      <InvitationAcceptanceContent />
    </Suspense>
  );
}
