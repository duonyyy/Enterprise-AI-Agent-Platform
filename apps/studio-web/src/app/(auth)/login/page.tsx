"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Input, BrandIcon } from "@enterprise/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@vinai.io");
  const [password, setPassword] = useState("••••••••••••");
  const [workspaceId, setWorkspaceId] = useState("default");
  const [isLoading, setIsLoading] = useState(false);

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setTimeout(() => {
      router.push(`/w/${workspaceId}/agents`);
    }, 600);
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
          maxWidth: "400px",
          backgroundColor: "var(--bg-surface)",
          border: "1px solid var(--border-strong)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-lg)",
          padding: "32px",
          display: "flex",
          flexDirection: "column",
          gap: "24px",
        }}
      >
        {/* Brand Header */}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: "10px" }}>
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
            <h1 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
              Enterprise Agent Studio
            </h1>
            <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: "4px 0 0 0" }}>
              Hệ thống Quản trị &amp; Điều phối Tác tử AI Doanh nghiệp
            </p>
          </div>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <Input
            label="Địa chỉ Email Doanh nghiệp"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="ten@doanhnghiep.vn"
          />

          <Input
            label="Mật khẩu Xác thực"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            placeholder="••••••••"
          />

          <Input
            label="Workspace ID"
            value={workspaceId}
            onChange={(e) => setWorkspaceId(e.target.value)}
            required
            placeholder="default"
            helperText="Default workspace: 'default' or 'ws_prod_01'"
          />

          <Button
            variant="primary"
            size="lg"
            type="submit"
            isLoading={isLoading}
            style={{ width: "100%", marginTop: "8px" }}
          >
            Đăng nhập Bảng điều khiển
          </Button>
        </form>

        {/* Security & Free SaaS Notice */}
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
