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
  isStreaming?: boolean;
  // Narrative streaming fields
  narrativeProducts?: Product[];
  visibleProductIds?: string[];
  introTexts?: Record<string, string>;
  summaryText?: string;
  statusMessage?: string;
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
  reportBehavior: (action: string, product: Product) => void;
  pendingOrder: Record<string, unknown> | null;
}

const API_BASE = "";

export function useChatStream(sessionId?: string): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [pendingOrder, setPendingOrder] = useState<Record<string, unknown> | null>(null);
  const sessionIdRef = useRef(sessionId || (typeof window !== "undefined" ? crypto.randomUUID() : ""));
  const userIdRef = useRef(
    typeof window !== "undefined"
      ? (localStorage.getItem("shopping_user_id") || (() => {
          const id = crypto.randomUUID();
          localStorage.setItem("shopping_user_id", id);
          return id;
        })())
      : ""
  );

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
          user_id: userIdRef.current,
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
      // Narrative streaming state
      let narrativeProducts: Product[] = [];
      let visibleProductIds: string[] = [];
      let introTexts: Record<string, string> = {};
      let summaryText = "";
      let streamingPhase: "intro" | "summary" | "" = "";
      let currentIntroProductId = "";
      let currentStatusMessage = "";

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
              }, (delta) => {
                currentContent += delta;
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
              }, {
                setNarrativeProducts: (products) => {
                  narrativeProducts = products;
                },
                startIntro: (pid) => {
                  streamingPhase = "intro";
                  currentIntroProductId = pid;
                  if (!introTexts[pid]) introTexts[pid] = "";
                },
                appendIntroDelta: (delta) => {
                  if (currentIntroProductId) {
                    introTexts[currentIntroProductId] = (introTexts[currentIntroProductId] || "") + delta;
                  }
                },
                showCard: (pid) => {
                  if (!visibleProductIds.includes(pid)) {
                    visibleProductIds = [...visibleProductIds, pid];
                  }
                },
                endIntro: () => {
                  streamingPhase = "";
                  currentIntroProductId = "";
                },
                startSummary: () => {
                  streamingPhase = "summary";
                  if (!summaryText) summaryText = "";
                },
                appendSummaryDelta: (delta) => {
                  summaryText += delta;
                },
                setStatus: (msg) => {
                  currentStatusMessage = msg;
                },
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
              narrativeProducts: narrativeProducts.length > 0 ? narrativeProducts : last.narrativeProducts,
              visibleProductIds: visibleProductIds.length > 0 ? visibleProductIds : last.visibleProductIds,
              introTexts: Object.keys(introTexts).length > 0 ? { ...introTexts } : last.introTexts,
              summaryText: summaryText || last.summaryText,
              statusMessage: currentStatusMessage || last.statusMessage,
              isLoading: false,
              isStreaming: true,
            };
          }
          return updated;
        });
      }

      // Stream finished — clear streaming flag
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last && last.role === "assistant" && last.isStreaming) {
          updated[updated.length - 1] = { ...last, isStreaming: false };
        }
        return updated;
      });
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
          user_id: userIdRef.current,
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
          user_id: userIdRef.current,
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

  const reportBehavior = useCallback((action: string, product: Product) => {
    // Fire-and-forget: non-blocking POST to behavior endpoint
    fetch(`${API_BASE}/api/behavior`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionIdRef.current,
        user_id: userIdRef.current,
        action,
        category: product.category || "",
        product_price: product.final_price || product.price,
        product_brand: product.brand || "",
        product_id: product.product_id || "",
      }),
    }).catch(() => {}); // silently ignore failures
  }, []);

  return { messages, isLoading, sendMessage, startOrder, resumeOrder, reportBehavior, pendingOrder };
}

interface NarrativeCallbacks {
  setNarrativeProducts: (products: Product[]) => void;
  startIntro: (pid: string) => void;
  appendIntroDelta: (delta: string) => void;
  showCard: (pid: string) => void;
  endIntro: () => void;
  startSummary: () => void;
  appendSummaryDelta: (delta: string) => void;
  setStatus: (msg: string) => void;
}

function handleSSEEvent(
  eventType: string,
  data: Record<string, unknown>,
  setContent: (content: string) => void,
  appendContent: (delta: string) => void,
  setProducts: (products: Product[]) => void,
  setRecommendations: (recs: Recommendation[]) => void,
  setPendingOrder: (order: Record<string, unknown> | null) => void,
  setOptions: (options: string[]) => void,
  setQuestions: (questions: ClarificationQuestion[]) => void,
  addToolCall?: (tc: ToolCall) => void,
  setResponseType?: (rt: string | undefined, rd: Record<string, unknown> | undefined) => void,
  narrative?: NarrativeCallbacks,
) {
  switch (eventType) {
    case "intent":
      break;
    case "status":
      if (narrative) {
        narrative.setStatus(data.message as string || "");
      }
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
    case "card_preload":
      if (narrative) {
        narrative.setNarrativeProducts((data.products as Product[]) || []);
      }
      break;
    case "product_intro_start":
      if (narrative) {
        narrative.startIntro(data.product_id as string || "");
      }
      break;
    case "text_delta":
      if (narrative) {
        narrative.appendIntroDelta((data.delta as string) || "");
      }
      break;
    case "product_card":
      if (narrative) {
        narrative.showCard(data.product_id as string || "");
      }
      break;
    case "product_intro_done":
      if (narrative) {
        narrative.endIntro();
      }
      break;
    case "summary_start":
      if (narrative) {
        narrative.startSummary();
      }
      break;
    case "summary_delta":
      if (narrative) {
        narrative.appendSummaryDelta((data.delta as string) || "");
      }
      break;
    case "explanation_delta":
      appendContent((data.delta as string) || "");
      break;
    case "explanation":
      // Compat: only use if no narrative products (non-recommendation path)
      if (!narrative) {
        setContent(data.text as string || "");
        setOptions([]);
        setQuestions([]);
      }
      break;
    case "interrupt":
      setPendingOrder(data);
      break;
    case "error":
      setContent((data.error as string) || "抱歉，处理过程中出现了问题，请稍后再试。");
      break;
  }
}
