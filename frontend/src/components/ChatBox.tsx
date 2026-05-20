"use client";

import { useState, useRef, useEffect } from "react";
import { useChatStream, ChatMessage, Product, Recommendation, ClarificationQuestion, ToolCall } from "@/hooks/useChatStream";
import ProductCard from "./ProductCard";
import ComparisonTable, { type ComparisonData } from "./ComparisonTable";
import OrderConfirm from "./OrderConfirm";

export default function ChatBox() {
  const { messages, isLoading, sendMessage, startOrder, resumeOrder, pendingOrder } = useChatStream();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    const text = input;
    setInput("");
    await sendMessage(text);
  };

  return (
    <div className="flex flex-col h-screen max-w-2xl mx-auto">
      {/* Header */}
      <div className="border-b px-4 py-3 bg-white">
        <h1 className="text-lg font-bold text-gray-900">ShoppingAgent</h1>
        <p className="text-xs text-gray-400">智能导购助手</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-20">
            <p className="text-lg mb-2">你好，我是 ShoppingAgent</p>
            <p className="text-sm">告诉我你想找什么，我来帮你推荐</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} onOrder={startOrder} onOptionClick={sendMessage} />
        ))}

        {isLoading && messages[messages.length - 1]?.role !== "assistant" && (
          <div className="flex gap-1 items-center text-gray-400 text-sm">
            <span className="animate-bounce">.</span>
            <span className="animate-bounce delay-100">.</span>
            <span className="animate-bounce delay-200">.</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* HITL Order Confirmation */}
      {pendingOrder && (
        <OrderConfirm
          productName={(pendingOrder.product_name as string) || "商品"}
          price={(pendingOrder.final_price as number) || 0}
          quantity={(pendingOrder.quantity as number) || 1}
          onConfirm={() => resumeOrder(true)}
          onCancel={() => resumeOrder(false)}
        />
      )}

      {/* Input */}
      <form onSubmit={handleSubmit} className="border-t px-4 py-3 bg-white">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="想找什么商品？"
            className="flex-1 border rounded-lg px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="px-5 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            发送
          </button>
        </div>
      </form>
    </div>
  );
}

function MessageBubble({ message, onOrder, onOptionClick }: { message: ChatMessage; onOrder?: (product: Product) => void; onOptionClick?: (text: string, displayText?: string) => void }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="bg-blue-600 text-white px-4 py-2.5 rounded-2xl rounded-br-sm max-w-[80%]">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-3">
        {message.isLoading && !message.content ? (
          <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-sm">
            <div className="flex gap-1">
              <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
              <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-100" />
              <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-200" />
            </div>
          </div>
        ) : (
          <>
            {message.toolCalls && message.toolCalls.length > 0 && (
              <ToolCallBubble toolCalls={message.toolCalls} />
            )}
            {message.responseType === "comparison_table" && message.responseData ? (
              <>
                <ComparisonTable data={message.responseData as unknown as ComparisonData} />
                {message.content && (
                  <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-sm text-sm leading-relaxed text-gray-600">
                    {message.content}
                  </div>
                )}
              </>
            ) : message.recommendations && message.recommendations.length > 0 && message.products ? (
              <>
                {/* Interleaved: recommendation text + product card pairs */}
                {message.recommendations.map((rec, i) => {
                  const product = message.products!.find((p) => p.product_id === rec.product_id);
                  if (!product) return null;
                  return (
                    <div key={rec.product_id || i} className="space-y-2">
                      <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-sm text-sm leading-relaxed">
                        {rec.text}
                      </div>
                      <ProductCard product={product} rank={i + 1} onOrder={onOrder} />
                    </div>
                  );
                })}
                {/* Summary text after all recommendations */}
                {message.content && (
                  <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-sm text-sm leading-relaxed text-gray-600">
                    {message.content}
                  </div>
                )}
              </>
            ) : (
              <>
                {/* Fallback: content first, then product grid */}
                {message.content && (
                  <div className="bg-gray-100 px-4 py-3 rounded-2xl rounded-bl-sm whitespace-pre-wrap text-sm leading-relaxed">
                    {message.content}
                  </div>
                )}
                {message.products && message.products.length > 0 && (
                  <div className="grid gap-2">
                    {message.products.map((product, i) => (
                      <ProductCard key={i} product={product} rank={i + 1} onOrder={onOrder} />
                    ))}
                  </div>
                )}
              </>
            )}
            {message.questions && message.questions.length > 0 && onOptionClick ? (
              <ClarificationForm questions={message.questions} onSubmit={onOptionClick} />
            ) : message.options && message.options.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {message.options.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => onOptionClick?.(opt)}
                    className="px-3 py-1.5 border border-blue-300 text-blue-600 text-sm rounded-full hover:bg-blue-50 transition-colors"
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ToolCallBubble({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false);

  const TOOL_LABELS: Record<string, string> = {
    product_search: "商品检索",
    product_detail_batch: "商品详情",
    price_compare: "价格对比",
    review_summary: "评论摘要",
    constraint_relaxation: "放宽条件",
    ask_clarification: "追问确认",
  };

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-2 bg-gray-50 flex items-center justify-between text-gray-500 hover:bg-gray-100 transition-colors"
      >
        <span>
          Agent 调用了 {toolCalls.length} 个工具
        </span>
        <span className="text-gray-400">{expanded ? "收起" : "展开"}</span>
      </button>
      {expanded && (
        <div className="px-3 py-2 space-y-1.5">
          {toolCalls.map((tc, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="text-blue-500 font-mono">{i + 1}.</span>
              <div>
                <span className="font-medium text-gray-700">
                  {TOOL_LABELS[tc.tool] || tc.tool}
                </span>
                {Object.keys(tc.args).length > 0 && (
                  <span className="text-gray-400 ml-1">
                    ({Object.entries(tc.args).slice(0, 2).map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 20) : v}`).join(", ")})
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ClarificationForm({ questions, onSubmit }: { questions: ClarificationQuestion[]; onSubmit: (json: string, displayText?: string) => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [step, setStep] = useState(0);

  const current = questions[step];
  const total = questions.length;
  const allAnswered = questions.every((q) => q.field === null || answers[q.field]);

  const handleSelect = (option: string) => {
    if (!current.field) return;
    setAnswers((prev) => ({ ...prev, [current.field!]: option }));
  };

  const handleNext = () => {
    if (step < total - 1) setStep(step + 1);
  };

  const handlePrev = () => {
    if (step > 0) setStep(step - 1);
  };

  const handleSubmit = () => {
    // Build friendly display text from selected options
    const displayParts = questions
      .filter((q) => q.field && answers[q.field])
      .map((q) => answers[q.field!]);
    const displayText = displayParts.join("，");
    onSubmit(JSON.stringify(answers), displayText);
  };

  return (
    <div className="bg-white border rounded-xl p-4 space-y-4 shadow-sm">
      <div className="text-xs text-gray-400">
        {step + 1}/{total} {Object.keys(answers).length > 0 && `· 已选 ${Object.keys(answers).length} 项`}
      </div>

      <div>
        <p className="text-sm font-medium text-gray-800 mb-3">{current.question}</p>
        {current.options.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {current.options.map((opt, i) => (
              <button
                key={i}
                onClick={() => handleSelect(opt)}
                className={`px-3 py-1.5 text-sm rounded-full border transition-colors ${
                  answers[current.field!] === opt
                    ? "bg-blue-600 text-white border-blue-600"
                    : "border-blue-300 text-blue-600 hover:bg-blue-50"
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-xs text-gray-400">请在下方输入框回答</p>
        )}
      </div>

      {/* 已选摘要 */}
      {Object.keys(answers).length > 0 && (
        <div className="text-xs text-gray-500">
          {Object.entries(answers).map(([k, v]) => (
            <span key={k} className="mr-2">✓ {v}</span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        {step > 0 && (
          <button onClick={handlePrev} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
            上一题
          </button>
        )}
        {step < total - 1 ? (
          <button
            onClick={handleNext}
            disabled={!current.field || !answers[current.field]}
            className="ml-auto px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50"
          >
            下一题
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!allAnswered}
            className="ml-auto px-4 py-1.5 text-sm bg-green-600 text-white rounded-lg disabled:opacity-50"
          >
            确认
          </button>
        )}
      </div>
    </div>
  );
}
