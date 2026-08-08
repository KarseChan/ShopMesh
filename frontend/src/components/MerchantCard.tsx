"use client";

import { useState } from "react";
import { Merchant } from "@/hooks/useChatStream";
import { useCart } from "@/contexts/CartContext";

interface Dish {
  product_id: string;
  name: string;
  price: number;
  category?: string;
}

interface MerchantCardProps {
  merchant: Merchant;
  rank?: number;
}

export default function MerchantCard({ merchant, rank }: MerchantCardProps) {
  const { addToCart } = useCart();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dishes, setDishes] = useState<Dish[] | null>(null);
  const [loading, setLoading] = useState(false);

  const toggleMenu = async () => {
    if (menuOpen) {
      setMenuOpen(false);
      return;
    }
    setMenuOpen(true);
    if (dishes === null) {
      setLoading(true);
      try {
        const res = await fetch(`/api/merchants/${encodeURIComponent(merchant.merchant_id)}/menu`);
        const d = await res.json();
        setDishes((d.dishes as Dish[]) || []);
      } catch {
        setDishes([]);
      } finally {
        setLoading(false);
      }
    }
  };

  const fee = merchant.delivery_fee;
  const feeTxt = !fee ? "免配送费" : `配送费 ¥${fee}`;

  return (
    <div className="border rounded-xl p-4 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-1.5">
        {rank && (
          <span className="inline-block bg-orange-100 text-orange-700 text-xs font-semibold px-2 py-1 rounded-full">
            #{rank}
          </span>
        )}
        {merchant.category && (
          <span className="inline-block bg-gray-100 text-gray-600 text-xs px-2 py-0.5 rounded">
            {merchant.category}
          </span>
        )}
        {merchant.rating !== undefined && (
          <span className="text-xs text-amber-600">★ {merchant.rating}</span>
        )}
      </div>

      <h3 className="font-semibold text-gray-900 mb-1.5">{merchant.name}</h3>

      {/* 关键秒送信息:距离 / 时效 / 配送费 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-700 mb-2">
        {merchant.delivery_minutes !== undefined && (
          <span className="font-medium text-green-600">约 {merchant.delivery_minutes} 分钟送达</span>
        )}
        {merchant.distance_km !== undefined && <span>{merchant.distance_km} km</span>}
        <span>{feeTxt}</span>
        {merchant.avg_price !== undefined && <span>人均 ¥{merchant.avg_price}</span>}
      </div>

      {merchant.rank_reason_text && (
        <p className="text-xs text-gray-600 bg-orange-50/60 rounded px-2 py-1 mb-2">
          {merchant.rank_reason_text}
          {merchant.open_hours ? `｜营业 ${merchant.open_hours}` : ""}
        </p>
      )}

      <button
        onClick={toggleMenu}
        className="w-full py-2 border border-orange-500 text-orange-600 text-sm font-medium rounded-lg hover:bg-orange-50 transition-colors"
      >
        {menuOpen ? "收起菜单" : "查看菜单 · 点单"}
      </button>

      {menuOpen && (
        <div className="mt-3 space-y-2">
          {loading && <p className="text-sm text-gray-400">加载菜单中…</p>}
          {!loading && dishes && dishes.length === 0 && (
            <p className="text-sm text-gray-400">该门店暂无可点菜品</p>
          )}
          {!loading &&
            dishes?.map((d) => (
              <div key={d.product_id} className="flex items-center justify-between gap-2 border-b last:border-b-0 pb-2">
                <div className="min-w-0">
                  <p className="text-sm text-gray-900 truncate">{d.name}</p>
                  <p className="text-sm font-semibold text-red-600">¥{d.price}</p>
                </div>
                <button
                  onClick={() => addToCart(d.product_id, 1)}
                  className="shrink-0 px-3 py-1.5 bg-orange-500 text-white text-sm rounded-lg hover:bg-orange-600 transition-colors"
                >
                  加入
                </button>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
