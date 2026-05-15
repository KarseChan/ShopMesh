"use client";

import { useState, useCallback, useRef } from "react";

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  products?: Product[];
  isLoading?: boolean;
}

export interface Product {
  name: string;
  price: number;
  final_price?: number;
  rank_score?: number;
  promo_desc?: string;
  suggest_message?: string;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  isLoading: boolean;
  sendMessage: (text: string) => Promise<void>;
  resumeOrder: (confirmed: boolean, data?: Record<string, unknown>) => Promise<void>;
  pendingOrder: Record<string, unknown> | null;
}

const API_BASE = "";

export function useChatStream(sessionId?: string): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [pendingOrder, setPendingOrder] = useState<Record<string, unknown> | null>(null);
  const sessionIdRef = useRef(sessionId || crypto.randomUUID());

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isLoading) return;

    // Add user message
    const userMsg: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);

    // Add loading placeholder
    const loadingMsg: ChatMessage = { role: "assistant", content: "", isLoading: true };
    setMessages((prev) => [...prev, loadingMsg]);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: sessionIdRef.current,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No reader");

      const decoder = new TextDecoder();
      let buffer = "";
      let currentContent = "";
      let currentProducts: Product[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7);
          } else if (line.startsWith("data: ")) {
            const jsonStr = line.slice(6);
            try {
              const data = JSON.parse(jsonStr);
              handleSSEEvent(eventType, data, (content) => {
                currentContent = content;
              }, (products) => {
                currentProducts = products;
              });
            } catch {
              // Skip malformed JSON
            }
          }
        }

        // Update assistant message
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last && last.role === "assistant") {
            updated[updated.length - 1] = {
              ...last,
              content: currentContent || last.content,
              products: currentProducts.length > 0 ? currentProducts : last.products,
              isLoading: false,
            };
          }
          return updated;
        });
      }
    } catch (error) {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last && last.role === "assistant") {
          updated[updated.length - 1] = {
            ...last,
            content: "抱歉，发生了错误，请重试。",
            isLoading: false,
          };
        }
        return updated;
      });
    } finally {
      setIsLoading(false);
    }
  }, [isLoading]);

  const resumeOrder = useCallback(async (
    confirmed: boolean,
    data?: Record<string, unknown>
  ) => {
    setPendingOrder(null);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/api/chat/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          confirmed,
          data: data || {},
        }),
      });

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";
      let resultContent = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const d = JSON.parse(line.slice(6));
              if (d.text) resultContent = d.text;
            } catch {}
          }
        }
      }

      if (resultContent) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: resultContent },
        ]);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { messages, isLoading, sendMessage, resumeOrder, pendingOrder };
}

function handleSSEEvent(
  eventType: string,
  data: Record<string, unknown>,
  setContent: (content: string) => void,
  setProducts: (products: Product[]) => void,
) {
  switch (eventType) {
    case "intent":
      // Could show intent indicator in UI
      break;
    case "clarification":
      setContent(data.question as string || data.explanation as string || "");
      break;
    case "results":
      setProducts((data.products as Product[]) || []);
      break;
    case "explanation":
      setContent(data.text as string || "");
      break;
    case "interrupt":
      // HITL: trigger confirmation dialog
      break;
    case "error":
      setContent(`错误：${data.error || "未知错误"}`);
      break;
  }
}
