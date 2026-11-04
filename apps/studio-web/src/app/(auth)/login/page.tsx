"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Input } from "@enterprise/ui";

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
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: "8px" }}>
          <div
            style={{
              width: "44px",
              height: "44px",
              borderRadius: "10px",
              backgroundColor: "var(--color-primary)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#FFFFFF",
              fontSize: "18px",
              fontWeight: 800,
              boxShadow: "0 2px 8px rgba(15, 118, 110, 0.25)",
            }}
          >
            AG
          </div>
          <h1 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            Enterprise Agent Studio
          </h1>
          <p style={{ fontSize: "12px", color: "var(--text-muted)", margin: 0 }}>
            Hệ thống Quản trị & Điều phối Tác tử AI Doanh nghiệp
          </p>
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
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            <span>Bảo mật Zero-Trust &amp; Phân quyền RBAC</span>
          </div>
          <div>Bản phân phối Doanh nghiệp Miễn phí (Free SaaS) — Không yêu cầu thanh toán.</div>
        </div>
      </div>
    </div>
  );
}
