"use client";

import { useState, useCallback, useRef } from "react";

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface ClarificationQuestion {
  field: string | null;
  question: string;
  options: string[];
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  products?: Product[];
  recommendations?: Recommendation[];
  options?: string[];
  questions?: ClarificationQuestion[];
  toolCalls?: ToolCall[];
  responseType?: string;
  responseData?: Record<string, unknown>;
  isLoading?: boolean;
}

export interface Product {
  product_id?: string;
  name: string;
  price: number;
  final_price?: number;
  platform_id?: string;
  category?: string;
  product_type?: string;
  brand?: string;
  image_url?: string;
  rank_score?: number;
  rank_reasons?: Record<string, number>;
  promo_desc?: string;
  suggest_message?: string;
  is_abnormal?: boolean;
  reputation?: number;
}

export interface Recommendation {
  product_id: string;
  text: string;
}

export interface UseChatStreamReturn {
  messages: ChatMessage[];
  isLoading: boolean;
  sendMessage: (text: string, displayText?: string) => Promise<void>;
  startOrder: (product: Product) => Promise<void>;
  resumeOrder: (confirmed: boolean) => Promise<void>;
  pendingOrder: Record<string, unknown> | null;
}

const API_BASE = "";

export function useChatStream(sessionId?: string): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [pendingOrder, setPendingOrder] = useState<Record<string, unknown> | null>(null);
  const sessionIdRef = useRef(sessionId || crypto.randomUUID());

  const sendMessage = useCallback(async (text: string, displayText?: string) => {
    if (!text.trim() || isLoading) return;

    // Add user message (show displayText if provided, otherwise raw text)
    const userMsg: ChatMessage = { role: "user", content: displayText || text };
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
      let currentRecommendations: Recommendation[] = [];
      let currentOptions: string[] = [];
      let currentQuestions: ClarificationQuestion[] = [];
      let currentToolCalls: ToolCall[] = [];
      let currentResponseType: string | undefined;
      let currentResponseData: Record<string, unknown> | undefined;

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
              }, (recs) => {
                currentRecommendations = recs;
              }, (orderData) => {
                setPendingOrder(orderData);
              }, (opts) => {
                currentOptions = opts;
              }, (qs) => {
                currentQuestions = qs;
              }, (tc) => {
                currentToolCalls = [...currentToolCalls, tc];
              }, (rt, rd) => {
                currentResponseType = rt;
                currentResponseData = rd;
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
              recommendations: currentRecommendations.length > 0 ? currentRecommendations : last.recommendations,
              options: currentOptions.length > 0 ? currentOptions : last.options,
              questions: currentQuestions.length > 0 ? currentQuestions : last.questions,
              toolCalls: currentToolCalls.length > 0 ? currentToolCalls : last.toolCalls,
              responseType: currentResponseType || last.responseType,
              responseData: currentResponseData || last.responseData,
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

  const startOrder = useCallback(async (product: Product) => {
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/api/chat/order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          product: {
            product_id: (product as unknown as Record<string, unknown>).product_id || product.name,
            name: product.name,
            price: product.price,
            final_price: product.final_price || product.price,
          },
        }),
      });

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7);
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (eventType === "interrupt") {
                setPendingOrder(data);
              }
            } catch {}
          }
        }
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

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

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7);
          } else if (line.startsWith("data: ")) {
            try {
              const d = JSON.parse(line.slice(6));
              if (eventType === "explanation" && d.text) {
                resultContent = d.text;
              }
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

  return { messages, isLoading, sendMessage, startOrder, resumeOrder, pendingOrder };
}

function handleSSEEvent(
  eventType: string,
  data: Record<string, unknown>,
  setContent: (content: string) => void,
  setProducts: (products: Product[]) => void,
  setRecommendations: (recs: Recommendation[]) => void,
  setPendingOrder: (order: Record<string, unknown> | null) => void,
  setOptions: (options: string[]) => void,
  setQuestions: (questions: ClarificationQuestion[]) => void,
  addToolCall?: (tc: ToolCall) => void,
  setResponseType?: (rt: string | undefined, rd: Record<string, unknown> | undefined) => void,
) {
  switch (eventType) {
    case "intent":
      break;
    case "tool_call":
      if (addToolCall) {
        addToolCall({ tool: data.tool as string || "", args: (data.args as Record<string, unknown>) || {} });
      }
      break;
    case "clarification":
      setContent(data.question as string || data.explanation as string || "");
      setOptions((data.options as string[]) || []);
      setQuestions((data.questions as ClarificationQuestion[]) || []);
      break;
    case "results":
      setProducts((data.products as Product[]) || []);
      if (data.recommendations) {
        setRecommendations(data.recommendations as Recommendation[]);
      }
      if (setResponseType && data.response_type) {
        setResponseType(data.response_type as string, data.response_data as Record<string, unknown>);
      }
      break;
    case "explanation":
      setContent(data.text as string || "");
      setOptions([]);
      setQuestions([]);
      break;
    case "interrupt":
      setPendingOrder(data);
      break;
    case "error":
      setContent(data.error || "抱歉，处理过程中出现了问题，请稍后再试。");
      break;
  }
}
