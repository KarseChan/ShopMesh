"use client";

import { useCallback, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";

interface OrderItem { product_id: string; name: string; price: number; qty: number; }
interface Order {
  order_id: string;
  status: string;
  total_price: number;
  items: OrderItem[];
  created_at: string;
}

const STATUS_LABEL: Record<string, string> = {
  created: "待支付",
  awaiting_payment: "待支付",
  paid: "已支付",
  // 秒送履约状态机(order_service.advance_fulfillment)
  preparing: "备餐中",
  delivering: "配送中",
  delivered: "已送达",
  shipped: "已发货",
  completed: "已完成",
  cancelled: "已取消",
  refunded: "已退款",
};
const STATUS_COLOR: Record<string, string> = {
  awaiting_payment: "bg-amber-100 text-amber-700",
  paid: "bg-green-100 text-green-700",
  preparing: "bg-amber-100 text-amber-700",
  delivering: "bg-blue-100 text-blue-700",
  delivered: "bg-green-100 text-green-700",
  completed: "bg-green-100 text-green-700",
  cancelled: "bg-gray-100 text-gray-500",
  refunded: "bg-gray-100 text-gray-500",
};

export default function Orders() {
  const { accessToken, user, isAuthenticated } = useAuth();
  const [isOpen, setOpen] = useState(false);
  const [orders, setOrders] = useState<Order[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const uid = user?.userId;

  const headers = useCallback(() => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (accessToken) h["Authorization"] = `Bearer ${accessToken}`;
    return h;
  }, [accessToken]);

  const load = useCallback(async () => {
    if (!isAuthenticated) return;
    const res = await fetch(`/api/orders?user_id=${encodeURIComponent(uid || "")}&limit=50`, { headers: headers() });
    const d = await res.json();
    setOrders(d.orders || []);
  }, [isAuthenticated, uid, headers]);

  const openDrawer = async () => {
    setOpen(true);
    await load();
  };

  const act = async (orderId: string, action: "refund" | "cancel") => {
    setBusy(orderId);
    await fetch(`/api/orders/${orderId}/${action}`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ user_id: uid }),
    });
    setBusy(null);
    await load();
  };

  if (!isAuthenticated) return null;

  return (
    <>
      <button
        onClick={openDrawer}
        className="fixed bottom-40 right-6 z-40 rounded-full bg-white border px-4 py-3 text-sm text-gray-700 shadow-lg hover:bg-gray-50"
      >
        📦 我的订单
      </button>

      {isOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setOpen(false)}>
          <div className="flex h-full w-full max-w-md flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="text-lg font-semibold">我的订单</h2>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {orders.length === 0 && <p className="mt-16 text-center text-gray-400">暂无订单</p>}
              {orders.map((o) => (
                <div key={o.order_id} className="rounded-lg border p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-mono text-xs text-gray-500">{o.order_id}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_COLOR[o.status] || "bg-gray-100 text-gray-600"}`}>
                      {STATUS_LABEL[o.status] || o.status}
                    </span>
                  </div>
                  <div className="space-y-0.5 text-sm text-gray-700">
                    {o.items.map((it) => (
                      <div key={it.product_id} className="flex justify-between">
                        <span className="truncate">{it.name} ×{it.qty}</span>
                        <span className="text-gray-400">¥{it.price}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 flex items-center justify-between border-t pt-2">
                    <span className="font-semibold text-red-600">¥{o.total_price}</span>
                    <div className="flex gap-2">
                      {o.status === "awaiting_payment" && (
                        <button
                          onClick={() => act(o.order_id, "cancel")}
                          disabled={busy === o.order_id}
                          className="rounded border px-3 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                        >取消</button>
                      )}
                      {o.status === "paid" && (
                        <button
                          onClick={() => act(o.order_id, "refund")}
                          disabled={busy === o.order_id}
                          className="rounded border border-red-300 px-3 py-1 text-xs text-red-500 hover:bg-red-50 disabled:opacity-50"
                        >退款</button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
