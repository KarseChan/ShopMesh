import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ShoppingAgent — 智能导购",
  description: "基于 LangGraph 的多 Agent 智能导购系统",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
