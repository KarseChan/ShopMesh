"use client";

import { useState } from "react";
import { useCart } from "@/contexts/CartContext";

export default function Cart() {
  const { cart, isOpen, open, close, updateQty, removeItem, checkout } = useCart();
  const [placing, setPlacing] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const onCheckout = async () => {
    setPlacing(true);
    setResult(null);
    const r = await checkout();
    setPlacing(false);
    setResult(r.ok ? `下单成功！订单号 ${r.order_id}，¥${r.total_price}（待支付）` : r.message || "下单失败");
  };

  return (
    <>
      {/* 悬浮购物车按钮 */}
      <button
        onClick={open}
        className="fixed bottom-24 right-6 z-40 flex items-center gap-2 rounded-full bg-blue-600 px-4 py-3 text-white shadow-lg hover:bg-blue-700"
        aria-label="购物车"
      >
        🛒
        {cart.count > 0 && (
          <span className="min-w-[20px] rounded-full bg-red-500 px-1.5 text-center text-xs font-bold">
            {cart.count}
          </span>
        )}
      </button>

      {/* 抽屉 */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={close}>
          <div
            className="flex h-full w-full max-w-md flex-col bg-white shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="text-lg font-semibold">购物车（{cart.count}）</h2>
              <button onClick={close} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {cart.items.length === 0 && (
                <p className="mt-16 text-center text-gray-400">购物车是空的</p>
              )}
              {cart.items.map((it) => (
                <div key={it.product_id} className="flex items-center gap-3 rounded-lg border p-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-gray-900">{it.name}</p>
                    <p className="text-sm text-red-600">¥{it.price}</p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => updateQty(it.product_id, it.qty - 1)}
                      className="h-7 w-7 rounded border text-gray-600 hover:bg-gray-50"
                    >−</button>
                    <span className="w-6 text-center text-sm">{it.qty}</span>
                    <button
                      onClick={() => updateQty(it.product_id, it.qty + 1)}
                      className="h-7 w-7 rounded border text-gray-600 hover:bg-gray-50"
                    >+</button>
                  </div>
                  <button
                    onClick={() => removeItem(it.product_id)}
                    className="ml-1 text-xs text-gray-400 hover:text-red-500"
                  >删除</button>
                </div>
              ))}
            </div>

            {result && (
              <div className="mx-4 mb-2 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700">
                {result}
              </div>
            )}

            <div className="border-t px-4 py-3">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-gray-500">合计</span>
                <span className="text-xl font-bold text-red-600">¥{cart.total}</span>
              </div>
              <button
                onClick={onCheckout}
                disabled={cart.items.length === 0 || placing}
                className="w-full rounded-lg bg-blue-600 py-2.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {placing ? "下单中…" : "确认下单"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
