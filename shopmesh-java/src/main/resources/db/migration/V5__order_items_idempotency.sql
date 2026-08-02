-- V5: 购物功能 P2 — order line items (items_json) + idempotency key.
-- Ported from the former Python Alembic revision a1c2e3f40501 as part of
-- consolidating schema ownership onto Flyway (single source of truth).
-- Idempotent DDL (IF NOT EXISTS) so it safely adopts a DB that already has
-- these columns (they were previously applied via a manual ALTER + Alembic stamp).

ALTER TABLE orders ADD COLUMN IF NOT EXISTS items_json TEXT NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR;
CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_idempotency_key ON orders(idempotency_key);
