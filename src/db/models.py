"""SQLModel data models — User / Product / UserProfile / Session / Order / IntentSample."""

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, unique=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    email: Optional[str] = None
    tenant_id: str = Field(index=True)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Product(SQLModel, table=True):
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: str = Field(index=True, unique=True)
    name: str
    category: str = Field(index=True)
    brand: str = Field(index=True)
    price: float
    stock: int = 0
    platform_id: str = Field(index=True)
    promotion_id: Optional[str] = None
    features: str = ""  # JSON array as string
    embedding_text: str = ""
    rating: float = 0.0
    delivery_minutes: int = 0
    # 秒送(V6): 商品/菜品挂到门店 + 区分实物/菜品
    merchant_id: Optional[str] = Field(default=None, index=True)
    item_type: str = "good"  # 'good' | 'dish'


class Merchant(SQLModel, table=True):
    """秒送门店 (V6). Relational SoT for 门店详情/下单履约; retrieval path uses the
    Qdrant `merchants` collection. Kept in sync with Flyway V6__merchants.sql."""

    __tablename__ = "merchants"

    id: Optional[int] = Field(default=None, primary_key=True)
    merchant_id: str = Field(index=True, unique=True)
    name: str
    city: str = Field(default="", index=True)
    latitude: float = 0.0
    longitude: float = 0.0
    category: str = Field(default="", index=True)  # 奶茶/快餐/超市便利/药店...
    rating: float = 0.0
    avg_price: float = 0.0
    delivery_fee: float = 0.0
    delivery_minutes: int = 0            # ETA
    delivery_radius_km: float = 3.0
    open_hour: int = 0                    # 营业起始小时
    close_hour: int = 24                  # 营业结束小时(>24 表示次日)
    open_hours: str = ""                  # 人读文本 "10:00-22:00"
    tags: str = ""                        # JSON array as string
    image_url: str = ""
    embedding_text: str = ""


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True, default="")
    user_id: str = Field(index=True)
    category: str = Field(index=True)  # per-category profile
    price_sensitivity: float = 0.5
    preferred_brands: str = ""  # JSON array as string
    visit_count: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        # Composite unique constraint: one profile per (user_id, category)
        unique_together = ("user_id", "category")


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True, default="")
    session_id: str = Field(index=True, unique=True)
    user_id: str = Field(index=True)
    status: str = "active"                              # active / idle / archived
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    turn_count: int = 0                                 # cumulative turns in this session
    last_extracted_turn: int = 0                        # last turn processed by batch preference extraction
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True, default="")
    order_id: str = Field(index=True, unique=True)
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    product_id: str = ""          # 兼容旧单品字段(购物车多商品见 items_json)
    quantity: int = 1
    total_price: float = 0.0
    # 购物车多商品明细 JSON: [{product_id, name, price, qty}]
    items_json: str = ""
    # 幂等键(同键重复下单返回同一订单),防网络重试重复扣款
    idempotency_key: Optional[str] = Field(default=None, index=True)
    # 状态机: created → awaiting_payment → paid → shipped → completed / cancelled / refunded
    status: str = "created"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IntentSample(SQLModel, table=True):
    __tablename__ = "intent_samples"

    id: Optional[int] = Field(default=None, primary_key=True)
    intent: str = Field(index=True)
    text: str
    source: str = "manual"  # manual / auto_learned
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessage(SQLModel, table=True):
    __tablename__ = "conversation_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True, default="")
    conversation_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str  # "user" / "assistant"
    content: str
    turn_id: int = 0                                    # which turn this message belongs to (shared by user+assistant)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PreferenceExtractionState(SQLModel, table=True):
    __tablename__ = "preference_extraction_state"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True, default="")
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    last_extracted_turn: int = 0
    last_extracted_at: datetime = Field(default_factory=datetime.utcnow)
    extraction_count: int = 0


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: Optional[int] = Field(default=None, primary_key=True)
    key_id: str = Field(index=True, unique=True)       # public identifier (prefix)
    key_hash: str                                       # bcrypt hash of full key
    tenant_id: str = Field(index=True)
    name: str = ""                                      # human-readable label
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
