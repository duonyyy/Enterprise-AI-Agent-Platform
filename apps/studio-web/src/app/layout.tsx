import React from "react";
import type { Metadata, Viewport } from "next";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "Enterprise Agent Studio — Bảng điều khiển Tác tử AI Doanh nghiệp",
  description: "Bảng điều khiển tự phục vụ và giám sát tác tử AI cấp doanh nghiệp",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <head>
        <meta charSet="utf-8" />
      </head>
      <body>
        <a href="#main-content" className="skip-to-content">
          Chuyển thẳng tới nội dung chính
        </a>
        {children}
      </body>
    </html>
  );
}
