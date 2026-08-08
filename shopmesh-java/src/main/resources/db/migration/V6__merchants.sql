-- V6: 秒送(对话式即时点单)P1 — merchants 门店表 + products.merchant_id/item_type.
-- 门店独立成表(经纬度 + 营业时段 + ETA + 配送半径/费用),秒送商品/菜品挂到门店。
-- 检索路径走 Qdrant `merchants` 集合(scripts/build_merchant_index.py);本表是
-- 门店详情/下单履约(P2)的关系型 source of truth。Flyway 是 schema 唯一 SoT
-- (docs/MIGRATIONS.md);SQLModel(src/db/models.py Merchant)与此保持一致,不驱动迁移。
-- 幂等 DDL(IF NOT EXISTS),可安全套用到已有该表/列的库。

CREATE TABLE IF NOT EXISTS merchants (
    id                  BIGSERIAL PRIMARY KEY,
    merchant_id         VARCHAR NOT NULL UNIQUE,
    name                VARCHAR NOT NULL,
    city                VARCHAR NOT NULL DEFAULT '',
    latitude            DOUBLE PRECISION NOT NULL,
    longitude           DOUBLE PRECISION NOT NULL,
    category            VARCHAR NOT NULL DEFAULT '',   -- 奶茶/快餐/超市便利/药店...
    rating              DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_price           DOUBLE PRECISION NOT NULL DEFAULT 0,
    delivery_fee        DOUBLE PRECISION NOT NULL DEFAULT 0,
    delivery_minutes    INTEGER NOT NULL DEFAULT 0,     -- ETA
    delivery_radius_km  DOUBLE PRECISION NOT NULL DEFAULT 3,
    open_hour           INTEGER NOT NULL DEFAULT 0,     -- 营业起始小时
    close_hour          INTEGER NOT NULL DEFAULT 24,    -- 营业结束小时(>24 表示次日,夜宵店)
    open_hours          VARCHAR NOT NULL DEFAULT '',    -- 人读文本 "10:00-22:00"
    tags                TEXT NOT NULL DEFAULT '',       -- JSON array as string
    image_url           VARCHAR NOT NULL DEFAULT '',
    embedding_text      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_merchants_merchant_id ON merchants(merchant_id);
CREATE INDEX IF NOT EXISTS ix_merchants_category ON merchants(category);
CREATE INDEX IF NOT EXISTS ix_merchants_city ON merchants(city);

-- 秒送商品/菜品 MVP 复用 products,加两列:挂到门店 + 区分实物/菜品。
ALTER TABLE products ADD COLUMN IF NOT EXISTS merchant_id VARCHAR;
ALTER TABLE products ADD COLUMN IF NOT EXISTS item_type VARCHAR NOT NULL DEFAULT 'good';  -- 'good' | 'dish'
CREATE INDEX IF NOT EXISTS ix_products_merchant_id ON products(merchant_id);
