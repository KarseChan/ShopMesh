"use client";

import { Product } from "@/hooks/useChatStream";
import { useCart } from "@/contexts/CartContext";

interface ProductCardProps {
  product: Product;
  rank?: number;
  onOrder?: (product: Product) => void;
  onProductClick?: (product: Product) => void;
}

const PLATFORM_NAMES: Record<string, string> = {
  jd: "京东",
  tb: "淘宝",
  pdd: "拼多多",
};

// 自包含占位图(data-URI,不依赖网络)。当 image_url 的图源不可达时兜底,
// 保证卡片图片位始终有内容,而不是留白或破图。
const FALLBACK_IMG =
  "data:image/svg+xml," +
  encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='300'>" +
      "<rect width='400' height='300' fill='#eef2f7'/>" +
      "<text x='200' y='150' font-family='sans-serif' font-size='22' fill='#94a3b8' " +
      "text-anchor='middle' dominant-baseline='middle'>商品图片</text></svg>"
  );

export default function ProductCard({ product, rank, onOrder, onProductClick }: ProductCardProps) {
  const { addToCart } = useCart();
  const displayPrice = product.final_price || product.price;
  const hasDiscount = product.final_price && product.final_price < product.price;
  const platformName = product.platform_id ? PLATFORM_NAMES[product.platform_id] || product.platform_id : "";

  return (
    <div
      className="border rounded-xl p-4 bg-white shadow-sm hover:shadow-md transition-shadow cursor-pointer"
      onClick={() => onProductClick?.(product)}
    >
      {product.image_url && (
        <div className="mb-3 -mx-4 -mt-4 overflow-hidden rounded-t-xl bg-gray-100 aspect-[4/3]">
          <img
            src={product.image_url}
            alt={product.name}
            loading="lazy"
            className="w-full h-full object-cover"
            onError={(e) => {
              // 图源不可达 → 换成自包含占位图(避免破图);已是占位图则不再重试
              if (e.currentTarget.src !== FALLBACK_IMG) e.currentTarget.src = FALLBACK_IMG;
            }}
          />
        </div>
      )}

      <div className="flex items-center gap-2 mb-2">
        {rank && (
          <span className="inline-block bg-blue-100 text-blue-700 text-xs font-semibold px-2 py-1 rounded-full">
            #{rank}
          </span>
        )}
        {platformName && (
          <span className="inline-block bg-gray-100 text-gray-600 text-xs px-2 py-0.5 rounded">
            {platformName}
          </span>
        )}
      </div>

      <h3 className="font-semibold text-gray-900 mb-1">{product.name}</h3>

      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-xl font-bold text-red-600">¥{displayPrice}</span>
        {hasDiscount && (
          <span className="text-sm text-gray-400 line-through">¥{product.price}</span>
        )}
      </div>

      {product.rank_reason_text && (
        <p className="text-sm text-gray-600 bg-blue-50/60 rounded px-2 py-1.5 mb-2">
          <span className="font-medium text-blue-600">推荐理由：</span>
          {product.rank_reason_text}
        </p>
      )}

      {product.is_abnormal && (
        <div className="bg-orange-50 border border-orange-200 rounded px-2 py-1.5 mb-2">
          <span className="text-orange-700 text-xs font-medium">
            {"⚠️"} 价格异常 — 实际到手价 ¥{displayPrice}，请注意运费或附加费用
          </span>
        </div>
      )}

      {product.promo_desc && (
        <span className="inline-block bg-red-50 text-red-600 text-xs px-2 py-0.5 rounded mb-2">
          {product.promo_desc}
        </span>
      )}

      {product.suggest_message && (
        <p className="text-sm text-amber-600 bg-amber-50 rounded px-2 py-1 mt-1">
          {product.suggest_message}
        </p>
      )}

      {product.rank_score !== undefined && (
        <div className="mt-2 flex items-center gap-1">
          <span className="text-xs text-gray-400">匹配度</span>
          <div className="flex-1 bg-gray-100 rounded-full h-1.5">
            <div
              className="bg-blue-500 h-1.5 rounded-full"
              style={{ width: `${Math.round(product.rank_score * 100)}%` }}
            />
          </div>
          <span className="text-xs text-gray-500">{Math.round(product.rank_score * 100)}%</span>
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            addToCart(product.product_id || product.name, 1);
          }}
          className="flex-1 py-2 border border-blue-600 text-blue-600 text-sm font-medium rounded-lg hover:bg-blue-50 transition-colors"
        >
          加入购物车
        </button>
        {onOrder && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOrder(product);
            }}
            className="flex-1 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            立即下单
          </button>
        )}
      </div>
    </div>
  );
}
