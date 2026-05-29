-- V1: Create all tables (migrated from Python Alembic)
-- This script creates the complete initial schema for ShopMesh.

-- Users (auth)
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    user_id         VARCHAR NOT NULL UNIQUE,
    username        VARCHAR NOT NULL UNIQUE,
    hashed_password VARCHAR NOT NULL,
    email           VARCHAR,
    tenant_id       VARCHAR NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);

-- API Keys (merchant integration)
CREATE TABLE IF NOT EXISTS api_keys (
    id           SERIAL PRIMARY KEY,
    key_id       VARCHAR NOT NULL UNIQUE,
    key_hash     VARCHAR NOT NULL,
    tenant_id    VARCHAR NOT NULL,
    name         VARCHAR NOT NULL DEFAULT '',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON api_keys(tenant_id);

-- Products (shared, no tenant_id)
CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    product_id     VARCHAR NOT NULL UNIQUE,
    name           VARCHAR NOT NULL,
    category       VARCHAR NOT NULL,
    brand          VARCHAR NOT NULL,
    price          DOUBLE PRECISION NOT NULL,
    stock          INTEGER NOT NULL DEFAULT 0,
    platform_id    VARCHAR NOT NULL,
    promotion_id   VARCHAR,
    features       TEXT NOT NULL DEFAULT '',
    embedding_text TEXT NOT NULL DEFAULT '',
    rating         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    delivery_minutes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_products_product_id ON products(product_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_platform_id ON products(platform_id);

-- User Profiles (tenant-scoped)
CREATE TABLE IF NOT EXISTS user_profiles (
    id               SERIAL PRIMARY KEY,
    tenant_id        VARCHAR NOT NULL DEFAULT '',
    user_id          VARCHAR NOT NULL,
    category         VARCHAR NOT NULL,
    price_sensitivity DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    preferred_brands TEXT NOT NULL DEFAULT '',
    visit_count      INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_id ON user_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_category ON user_profiles(category);

-- Sessions (tenant-scoped)
CREATE TABLE IF NOT EXISTS sessions (
    id         SERIAL PRIMARY KEY,
    tenant_id  VARCHAR NOT NULL DEFAULT '',
    session_id VARCHAR NOT NULL UNIQUE,
    user_id    VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant_id ON sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- Orders (tenant-scoped)
CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    tenant_id   VARCHAR NOT NULL DEFAULT '',
    order_id    VARCHAR NOT NULL UNIQUE,
    session_id  VARCHAR NOT NULL,
    user_id     VARCHAR NOT NULL,
    product_id  VARCHAR NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    total_price DOUBLE PRECISION NOT NULL,
    status      VARCHAR NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_id ON orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_session_id ON orders(session_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);

-- Intent Samples (shared, no tenant_id)
CREATE TABLE IF NOT EXISTS intent_samples (
    id         SERIAL PRIMARY KEY,
    intent     VARCHAR NOT NULL,
    text       TEXT NOT NULL,
    source     VARCHAR NOT NULL DEFAULT 'manual',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intent_samples_intent ON intent_samples(intent);

-- Conversation Messages (tenant-scoped)
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              SERIAL PRIMARY KEY,
    tenant_id       VARCHAR NOT NULL DEFAULT '',
    conversation_id VARCHAR NOT NULL,
    user_id         VARCHAR NOT NULL,
    role            VARCHAR NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_tenant_id ON conversation_messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_id ON conversation_messages(user_id);
