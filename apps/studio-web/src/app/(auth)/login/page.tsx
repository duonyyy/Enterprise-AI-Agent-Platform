"use client";

import React, { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button, BrandIcon } from "@enterprise/ui";
import { defaultApiClient, CurrentUserResponse } from "@enterprise/api-client";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isLoading, setIsLoading] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUserResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    // Check error in search params
    const err = searchParams?.get("error");
    const desc = searchParams?.get("error_description");
    if (err === "session_expired") {
      setErrorMessage("Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.");
    } else if (err === "unauthorized" || err === "auth_failed") {
      setErrorMessage("Xác thực không thành công hoặc phiên làm việc đã bị thu hồi.");
    } else if (err) {
      setErrorMessage(desc ? `Lỗi xác thực: ${desc}` : `Đăng nhập không thành công (${err}).`);
    }

    // Check if user is already authenticated
    defaultApiClient
      .getCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        if (user.workspaces && user.workspaces.length > 0) {
          const targetWorkspace = user.workspaces[0]?.workspace_id;
          if (targetWorkspace) {
            router.push(`/w/${targetWorkspace}/agents`);
          }
        }
      })
      .catch((err: unknown) => {
        // Distinguish unauthenticated (401) from backend infrastructure failure (503)
        setCurrentUser(null);
        if (err && typeof err === "object" && "status" in err && (err as { status: number }).status !== 401) {
          setErrorMessage("Dịch vụ xác thực hoặc máy chủ đang gián đoạn. Vui lòng thử lại sau.");
        }
      });
  }, [router, searchParams]);

  const handleOidcLogin = async () => {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const returnTo = searchParams?.get("return_to") || "/login";
      const { authorization_url } = await defaultApiClient.getLoginUrl(returnTo);
      if (authorization_url) {
        window.location.href = authorization_url;
      } else {
        window.location.href = `/v1/auth/login?redirect=true&return_to=${encodeURIComponent(returnTo)}`;
      }
    } catch (err: unknown) {
      setIsLoading(false);
      setErrorMessage(
        err instanceof Error
          ? `Không thể kết nối tới Identity Provider: ${err.message}`
          : "Không thể khởi tạo phiên đăng nhập OIDC."
      );
    }
  };

  const handleLogout = async () => {
    setIsLoading(true);
    try {
      await defaultApiClient.logout();
      setCurrentUser(null);
      setErrorMessage("Đã đăng xuất thành công.");
    } catch {
      setErrorMessage("Lỗi khi đăng xuất.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
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
          maxWidth: "440px",
          backgroundColor: "var(--bg-surface)",
          border: "1px solid var(--border-strong)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-lg)",
          padding: "36px",
          display: "flex",
          flexDirection: "column",
          gap: "24px",
        }}
      >
        {/* Brand Header */}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: "12px" }}>
          <div
            style={{
              width: "56px",
              height: "44px",
              borderRadius: "10px",
              backgroundColor: "var(--color-primary-subtle)",
              border: "1px solid var(--color-primary-border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 2px 8px rgba(15, 118, 110, 0.15)",
            }}
          >
            <svg
              width="38"
              height="24"
              viewBox="90 100 820 520"
              role="img"
              aria-label="Enterprise Agent Platform Logo"
            >
              <path
                d="M110 603 C101 603 101 597 107 581 L158 482 L362 482 L395 410 L196 410 L252 297 L482 297 L520 223 L291 223 L333 140 C339 125 348 115 366 115 L636 115 C654 115 664 121 673 139 L890 581 C901 602 889 607 877 607 L664 607 L643 417 L548 365 L459 417 L437 607 L125 607 C116 607 112 606 110 603 Z"
                fill="#0F766E"
              />
              <path d="M493 607 L493 439 L548 409 L602 439 L602 607 Z" fill="#C2410C" />
            </svg>
          </div>
          <div>
            <h1 style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
              Enterprise Agent Studio
            </h1>
            <p style={{ fontSize: "13px", color: "var(--text-muted)", margin: "6px 0 0 0" }}>
              Hệ thống Quản trị &amp; Điều phối Tác tử AI Doanh nghiệp
            </p>
          </div>
        </div>

        {/* Error Alert */}
        {errorMessage && (
          <div
            role="alert"
            style={{
              padding: "12px 16px",
              borderRadius: "var(--radius-md)",
              backgroundColor: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              color: "#dc2626",
              fontSize: "13px",
              lineHeight: 1.4,
            }}
          >
            {errorMessage}
          </div>
        )}

        {/* Authenticated user but no workspaces */}
        {currentUser && currentUser.workspaces.length === 0 && (
          <div
            style={{
              padding: "14px 16px",
              borderRadius: "var(--radius-md)",
              backgroundColor: "rgba(245, 158, 11, 0.1)",
              border: "1px solid rgba(245, 158, 11, 0.3)",
              color: "#b45309",
              fontSize: "13px",
              lineHeight: 1.5,
              display: "flex",
              flexDirection: "column",
              gap: "8px",
            }}
          >
            <div>
              <strong>Đã xác thực:</strong> {currentUser.display_name || currentUser.email_normalized || currentUser.subject}
            </div>
            <div>
              Tài khoản của bạn chưa thuộc bất kỳ Không gian làm việc (Workspace) nào. Vui lòng liên hệ Quản trị viên để được cấp quyền hoặc kiểm tra hộp thư lời mời.
            </div>
            <Button variant="secondary" size="sm" onClick={handleLogout} isLoading={isLoading}>
              Đăng xuất
            </Button>
          </div>
        )}

        {/* Login Action Area */}
        {(!currentUser || currentUser.workspaces.length > 0) && (
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <Button
              variant="primary"
              size="lg"
              onClick={handleOidcLogin}
              isLoading={isLoading}
              style={{ width: "100%", height: "46px", fontSize: "14px", fontWeight: 600 }}
            >
              Đăng nhập qua Enterprise SSO (OIDC)
            </Button>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: "12px",
                gap: "12px",
              }}
            >
              <div style={{ flex: 1, height: "1px", backgroundColor: "var(--border-subtle)" }} />
              <span>Giao thức Chuẩn Doanh nghiệp</span>
              <div style={{ flex: 1, height: "1px", backgroundColor: "var(--border-subtle)" }} />
            </div>

            <div
              style={{
                backgroundColor: "var(--bg-canvas)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "12px 14px",
                fontSize: "12px",
                color: "var(--text-secondary)",
                lineHeight: 1.5,
              }}
            >
              <div>• Chuẩn xác thực: <strong>OpenID Connect (OIDC)</strong></div>
              <div>• Bảo vệ mã ủy quyền: <strong>PKCE S256 (RFC 7636)</strong></div>
              <div>• Phiên làm việc: <strong>HTTP-Only Secure Cookie</strong></div>
            </div>
          </div>
        )}

        {/* Security & Compliance Notice */}
        <div
          style={{
            borderTop: "1px solid var(--border-subtle)",
            paddingTop: "16px",
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            textAlign: "center",
            fontSize: "11px",
            color: "var(--text-muted)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "6px" }}>
            <BrandIcon name="safety-shield" size={14} color="var(--color-primary)" />
            <span>Bảo mật Zero-Trust &amp; Phân quyền RBAC</span>
          </div>
          <div>Bản phân phối Doanh nghiệp Miễn phí (Free SaaS) — Không yêu cầu thanh toán.</div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div style={{ minHeight: "100vh", backgroundColor: "var(--bg-canvas)" }} />}>
      <LoginForm />
    </Suspense>
  );
}
