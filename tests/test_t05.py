"""T0.5 model factory + data layer tests."""

from src.models.llm_client import LLMClient, get_llm
from src.db.models import Product, UserProfile, SessionRecord, Order, IntentSample


def test_llm_factory_returns_cached_instance():
    """get_llm returns the same instance for the same agent name."""
    a = get_llm("default")
    b = get_llm("default")
    assert a is b


def test_llm_factory_different_agents():
    """Different agent names can have different configs (falls back to default)."""
    a = get_llm("default")
    b = get_llm("nonexistent_agent")
    # Both should exist but may be same config if fallback
    assert isinstance(a, LLMClient)
    assert isinstance(b, LLMClient)


def test_llm_client_config():
    """LLMClient reads config correctly."""
    client = get_llm()
    assert client.model  # not empty
    assert client.base_url  # not empty
    assert client.temperature >= 0


def test_sqlmodel_tables_defined():
    """All SQLModel tables have correct table names."""
    assert Product.__tablename__ == "products"
    assert UserProfile.__tablename__ == "user_profiles"
    assert SessionRecord.__tablename__ == "sessions"
    assert Order.__tablename__ == "orders"
    assert IntentSample.__tablename__ == "intent_samples"


def test_product_fields():
    """Product model has required fields."""
    p = Product(
        product_id="test_001",
        name="Test Product",
        category="test",
        brand="TestBrand",
        price=9.99,
    )
    assert p.product_id == "test_001"
    assert p.price == 9.99
