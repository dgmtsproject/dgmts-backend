-- Run ONCE on an existing server (as the postgres superuser) to add saved chatbot
-- conversations. Already included in projects_schema.sql for fresh installs.
-- ---------- Saved chatbot conversations (private to each user) ----------
CREATE TABLE IF NOT EXISTS projects.chat_conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   TEXT NOT NULL,            -- company user_id (or 'superuser')
    title      TEXT NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_conversations_owner ON projects.chat_conversations (owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS projects.chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES projects.chat_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON projects.chat_messages (conversation_id, id);

GRANT SELECT, INSERT, UPDATE, DELETE ON projects.chat_conversations, projects.chat_messages TO dgmts_projects_user;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE projects.chat_messages_id_seq TO dgmts_projects_user;
