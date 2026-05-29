-- V2: Add LLM usage tracking table for cost monitoring
-- Records every LLM API call with token counts and calculated costs.

CREATE TABLE IF NOT EXISTS llm_usage (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       VARCHAR NOT NULL,
    model           VARCHAR NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_date
    ON llm_usage(tenant_id, created_at);
