-- V4: Session lifecycle — status, turn tracking, preference extraction state

-- Extend sessions table with lifecycle fields
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'active';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS turn_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_extracted_turn INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active_at ON sessions(last_active_at);

-- Extend conversation_messages with turn_id
ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS turn_id INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_conversation_messages_turn_id ON conversation_messages(conversation_id, turn_id);

-- Preference extraction state tracking
CREATE TABLE IF NOT EXISTS preference_extraction_state (
    id                   SERIAL PRIMARY KEY,
    tenant_id            VARCHAR NOT NULL DEFAULT '',
    session_id           VARCHAR NOT NULL,
    user_id              VARCHAR NOT NULL,
    last_extracted_turn  INTEGER NOT NULL DEFAULT 0,
    last_extracted_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    extraction_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pref_extract_tenant_id ON preference_extraction_state(tenant_id);
CREATE INDEX IF NOT EXISTS idx_pref_extract_session_id ON preference_extraction_state(session_id);
CREATE INDEX IF NOT EXISTS idx_pref_extract_user_id ON preference_extraction_state(user_id);
