"use client";

interface Product {
  name: string;
  price: number;
  final_price?: number;
  rank_score?: number;
  promo_desc?: string;
  suggest_message?: string;
}

interface ProductCardProps {
  product: Product;
  rank?: number;
}

export default function ProductCard({ product, rank }: ProductCardProps) {
  const displayPrice = product.final_price || product.price;
  const hasDiscount = product.final_price && product.final_price < product.price;

  return (
    <div className="border rounded-xl p-4 bg-white shadow-sm hover:shadow-md transition-shadow">
      {rank && (
        <span className="inline-block bg-blue-100 text-blue-700 text-xs font-semibold px-2 py-1 rounded-full mb-2">
          #{rank}
        </span>
      )}

      <h3 className="font-semibold text-gray-900 mb-1">{product.name}</h3>

      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-xl font-bold text-red-600">¥{displayPrice}</span>
        {hasDiscount && (
          <span className="text-sm text-gray-400 line-through">¥{product.price}</span>
        )}
      </div>

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
    </div>
  );
}
