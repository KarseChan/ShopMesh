"use client";

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from "react";
import { useAuth } from "./AuthContext";

export interface CartItem {
  product_id: string;
  name: string;
  price: number;
  qty: number;
}
export interface Cart {
  items: CartItem[];
  count: number;
  total: number;
}
export interface OrderResult {
  ok: boolean;
  message?: string;
  order_id?: string;
  total_price?: number;
  status?: string;
}

interface CartCtx {
  cart: Cart;
  isOpen: boolean;
  open: () => void;
  close: () => void;
  addToCart: (productId: string, qty?: number) => Promise<void>;
  updateQty: (productId: string, qty: number) => Promise<void>;
  removeItem: (productId: string) => Promise<void>;
  refresh: () => Promise<void>;
  checkout: () => Promise<OrderResult>;
  pay: (orderId: string) => Promise<OrderResult>;
}

const Ctx = createContext<CartCtx | null>(null);
const EMPTY: Cart = { items: [], count: 0, total: 0 };

export function CartProvider({ children }: { children: ReactNode }) {
  const { accessToken, user, isAuthenticated } = useAuth();
  const [cart, setCart] = useState<Cart>(EMPTY);
  const [isOpen, setOpen] = useState(false);
  const uid = user?.userId;

  const headers = useCallback(() => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (accessToken) h["Authorization"] = `Bearer ${accessToken}`;
    return h;
  }, [accessToken]);

  const applyCart = (d: { items?: CartItem[]; count?: number; total?: number }) =>
    setCart({ items: d.items || [], count: d.count || 0, total: d.total || 0 });

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setCart(EMPTY);
      return;
    }
    try {
      const res = await fetch(`/api/cart?user_id=${encodeURIComponent(uid || "")}`, { headers: headers() });
      applyCart(await res.json());
    } catch {
      /* ignore */
    }
  }, [isAuthenticated, uid, headers]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const post = useCallback(
    async (path: string, body: object) => {
      const res = await fetch(path, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ ...body, user_id: uid, session_id: uid }),
      });
      return res.json();
    },
    [headers, uid]
  );

  const addToCart = useCallback(
    async (productId: string, qty = 1) => {
      const d = await post("/api/cart/add", { product_id: productId, quantity: qty });
      if (d.items) applyCart(d);
      setOpen(true);
    },
    [post]
  );

  const updateQty = useCallback(
    async (productId: string, qty: number) => {
      const d = await post("/api/cart/update", { product_id: productId, quantity: qty });
      if (d.items) applyCart(d);
    },
    [post]
  );

  const removeItem = useCallback(
    async (productId: string) => {
      const d = await post("/api/cart/remove", { product_id: productId });
      if (d.items) applyCart(d);
    },
    [post]
  );

  const checkout = useCallback(async (): Promise<OrderResult> => {
    // 幂等键:同一次结算重试不会重复下单
    const idem = `idem-${uid}-${Date.now()}`;
    const res: OrderResult = await post("/api/orders", { confirmed: true, idempotency_key: idem });
    if (res.ok) await refresh();
    return res;
  }, [post, uid, refresh]);

  const pay = useCallback(
    async (orderId: string): Promise<OrderResult> => {
      // 1. 创建支付会话(真实场景返回支付网关 hosted checkout URL,跳转过去付款)
      const sess = await fetch(`/api/orders/${orderId}/pay`, { method: "POST", headers: headers() }).then((r) => r.json());
      if (!sess.ok) return sess;
      // 2. mock:调用"支付方"模拟页完成付款 → 支付方回调 webhook → 订单转 paid
      return fetch(sess.pay_url, { method: "POST", headers: headers() }).then((r) => r.json());
    },
    [headers]
  );

  return (
    <Ctx.Provider value={{ cart, isOpen, open: () => setOpen(true), close: () => setOpen(false), addToCart, updateQty, removeItem, refresh, checkout, pay }}>
      {children}
    </Ctx.Provider>
  );
}

export function useCart() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useCart must be used within CartProvider");
  return c;
}
