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


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
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
    session_id: str = Field(index=True, unique=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: str = Field(index=True, unique=True)
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    product_id: str
    quantity: int = 1
    total_price: float
    status: str = "pending"  # pending / confirmed / cancelled
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
    conversation_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str  # "user" / "assistant"
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
