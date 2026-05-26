"""Response Schemas — Pydantic models for each agent's Final Answer format.

Used to validate LLM output before returning to frontend.
On validation failure, a repair prompt is generated and the LLM is asked to fix.
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError


# --- Recommendation Agent ---

class RecommendationItem(BaseModel):
    product_id: str
    text: str
    rank: int = 1


class RecommendationResponse(BaseModel):
    response_type: Literal["recommendation_cards"]
    recommendations: list[RecommendationItem]
    summary: str


class SelectionResponse(BaseModel):
    """New format: agent only selects product IDs, narrative writer generates text."""
    response_type: Literal["recommendation_cards"]
    selected_product_ids: list[str]


# --- Search Agent ---

class ProductGridItem(BaseModel):
    product_id: str
    match_type: Literal["exact", "supplemental"] = "exact"


class ProductGridResponse(BaseModel):
    response_type: Literal["product_grid"]
    products: list[ProductGridItem]
    total: int
    summary: str


# --- Detail Agent ---

class DetailCardResponse(BaseModel):
    response_type: Literal["detail_card"]
    product_id: str
    detail: dict
    review: dict
    verdict: str
    summary: str


# --- Compare Agent ---

class ComparisonTableItem(BaseModel):
    product_id: str
    price: float = 0.0
    rating: float = 0.0
    capacity: str | None = None
    material: str | None = None
    style: str | None = None
    scenario_fit: str | None = None
    reputation_label: str | None = None
    selling_points: list[str] = []
    concerns: list[str] = []


class ComparisonTableResponse(BaseModel):
    response_type: Literal["comparison_table"]
    products: list[ComparisonTableItem]
    best_value: str
    verdict: str
    summary: str


# --- Schema registry ---

RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "recommendation_cards": RecommendationResponse,
    "product_grid": ProductGridResponse,
    "detail_card": DetailCardResponse,
    "comparison_table": ComparisonTableResponse,
}


def parse_response_json(raw_text: str, response_type: str) -> dict | None:
    """Extract and validate JSON from LLM output.

    Returns parsed dict if valid, None if parse or validation fails.
    """
    # Extract JSON from possible code fences
    json_str = raw_text
    if "```json" in raw_text:
        json_str = raw_text.split("```json")[1].split("```")[0].strip()
    elif "```" in raw_text:
        json_str = raw_text.split("```")[1].split("```")[0].strip()

    # Fallback: find first { ... } block
    if not json_str.strip().startswith("{"):
        import re
        match = re.search(r"\{[\s\S]*\}", json_str)
        if match:
            json_str = match.group(0)

    try:
        parsed = json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        return None

    # Validate against schema if available
    schema = RESPONSE_SCHEMAS.get(response_type)
    if schema:
        try:
            schema.model_validate(parsed)
        except ValidationError:
            # Fallback: try SelectionResponse for recommendation_cards
            if response_type == "recommendation_cards":
                try:
                    SelectionResponse.model_validate(parsed)
                except ValidationError:
                    return None
            else:
                return None

    return parsed


def build_repair_prompt(raw_text: str, response_type: str, error: str) -> str:
    """Build a repair prompt asking the LLM to fix its JSON output."""
    schema = RESPONSE_SCHEMAS.get(response_type)
    schema_desc = ""
    if schema:
        schema_desc = f"\n期望的 JSON schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}"

    return (
        f"你的上一次输出 JSON 格式有误，无法解析。\n"
        f"错误: {error}\n"
        f"你的输出:\n{raw_text[:500]}\n"
        f"{schema_desc}\n"
        f"请严格按照 schema 重新输出 JSON，不要输出其他文字。"
    )
