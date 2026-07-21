"use client";

interface CompareProduct {
  product_id: string;
  name: string;
  price: number;
  rating?: number;
  capacity?: string;
  style?: string;
  scenario_fit?: string;
  reputation_label?: string;
  selling_points?: string[];
  concerns?: string[];
}

export interface ComparisonData {
  products: CompareProduct[];
  best_value?: string;
  verdict?: string;
  summary?: string;
}

export default function ComparisonTable({ data }: { data: ComparisonData }) {
  const { products, best_value, verdict } = data;
  if (!products || products.length < 2) return null;

  const dimensions = [
    { label: "价格", key: "price", format: (v: number) => `¥${v}` },
    { label: "口碑评分", key: "rating", format: (v: number) => `${(v * 100).toFixed(0)}分` },
    { label: "容量", key: "capacity" },
    { label: "风格", key: "style" },
    { label: "适用场景", key: "scenario_fit" },
    { label: "口碑", key: "reputation_label" },
    { label: "亮点", key: "selling_points", format: (v: string[]) => v.join("、") },
    { label: "注意事项", key: "concerns", format: (v: string[]) => v.join("、") },
  ];

  return (
    <div className="space-y-3">
      {/* Comparison table */}
      <div className="border rounded-xl overflow-hidden bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50">
              <th className="text-left px-4 py-2.5 text-gray-500 font-medium w-24">对比项</th>
              {products.map((p) => (
                <th key={p.product_id} className="text-left px-4 py-2.5 font-semibold text-gray-900">
                  <div className="flex items-center gap-1.5">
                    {p.name}
                    {best_value === p.product_id && (
                      <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">性价比</span>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dimensions.map((dim) => {
              const values = products.map((p) => {
                const val = (p as unknown as Record<string, unknown>)[dim.key];
                if (val == null || (Array.isArray(val) && val.length === 0)) return null;
                return dim.format ? dim.format(val as never) : String(val);
              });
              // Skip rows where all values are null
              if (values.every((v) => v == null)) return null;

              return (
                <tr key={dim.key} className="border-t">
                  <td className="px-4 py-2 text-gray-500 font-medium">{dim.label}</td>
                  {values.map((v, i) => {
                    const isPrice = dim.key === "price";
                    const isBest = isPrice && products.length === 2 && products[i].price === Math.min(...products.map((p) => p.price));
                    return (
                      <td key={i} className={`px-4 py-2 ${isBest ? "text-green-600 font-semibold" : "text-gray-800"}`}>
                        {v || "-"}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Verdict */}
      {verdict && (
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3 text-sm text-blue-800 leading-relaxed">
          {verdict}
        </div>
      )}
    </div>
  );
}
