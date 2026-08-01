"use client";

import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import ChatBox from "@/components/ChatBox";
import Cart from "@/components/Cart";

export default function Home() {
  const { isAuthenticated, isLoading, logout, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-pulse text-gray-400">加载中...</div>
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      {/* Top bar with user info */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b text-sm shrink-0">
        <span className="text-gray-500">
          {user?.username}
        </span>
        <button
          onClick={logout}
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          退出
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <ChatBox />
      </div>
      <Cart />
    </div>
  );
}
