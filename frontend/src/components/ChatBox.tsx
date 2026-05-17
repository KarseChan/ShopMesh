"use client";

import { useState, useRef, useEffect } from "react";
import { useChatStream, ChatMessage, Product } from "@/hooks/useChatStream";
import ProductCard from "./ProductCard";
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
          <MessageBubble key={i} message={msg} onOrder={startOrder} />
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

function MessageBubble({ message, onOrder }: { message: ChatMessage; onOrder?: (product: Product) => void }) {
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
      </div>
    </div>
  );
}
