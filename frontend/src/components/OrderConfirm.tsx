"use client";

import { useState, useEffect } from "react";

interface OrderConfirmProps {
  productName: string;
  price: number;
  quantity?: number;
  onConfirm: () => void;
  onCancel: () => void;
  timeoutSeconds?: number;
}

export default function OrderConfirm({
  productName,
  price,
  quantity = 1,
  onConfirm,
  onCancel,
  timeoutSeconds = 300,
}: OrderConfirmProps) {
  const [remaining, setRemaining] = useState(timeoutSeconds);

  useEffect(() => {
    if (remaining <= 0) {
      onCancel();
      return;
    }
    const timer = setInterval(() => setRemaining((r) => r - 1), 1000);
    return () => clearInterval(timer);
  }, [remaining, onCancel]);

  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl p-6 max-w-sm w-full mx-4 shadow-xl">
        <h2 className="text-lg font-bold text-gray-900 mb-4">确认下单</h2>

        <div className="space-y-3 mb-6">
          <div className="flex justify-between">
            <span className="text-gray-500">商品</span>
            <span className="font-medium">{productName}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">数量</span>
            <span>{quantity}</span>
          </div>
          <div className="flex justify-between border-t pt-2">
            <span className="text-gray-500">应付金额</span>
            <span className="text-xl font-bold text-red-600">¥{price}</span>
          </div>
        </div>

        <div className="text-center text-sm text-gray-400 mb-4">
          {minutes > 0
            ? `${minutes}分${seconds.toString().padStart(2, "0")}秒后自动取消`
            : `${seconds}秒后自动取消`}
        </div>

        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 py-2.5 border border-gray-300 rounded-lg text-gray-700 font-medium hover:bg-gray-50 transition-colors"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            确认支付
          </button>
        </div>
      </div>
    </div>
  );
}
