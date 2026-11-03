-- Applied at startup. Every statement is idempotent.

CREATE TABLE IF NOT EXISTS conversations (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title       text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- The sidebar lists the most recently active conversations first.
CREATE INDEX IF NOT EXISTS conversations_updated_at_idx
    ON conversations (updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS messages (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id  bigint NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role             text NOT NULL CHECK (role IN ('user', 'assistant')),
    content          text NOT NULL,
    status           text NOT NULL DEFAULT 'complete'
                     CHECK (status IN ('complete', 'cancelled', 'error')),
    model            text,
    ttft_ms          double precision CHECK (ttft_ms >= 0),
    total_ms         double precision CHECK (total_ms >= 0),
    output_tokens    integer CHECK (output_tokens >= 0),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Timings and the model name belong to replies, never to what the user typed.
    CHECK (role = 'assistant' OR (model IS NULL AND ttft_ms IS NULL AND total_ms IS NULL
                                  AND output_tokens IS NULL AND status = 'complete')),
    CHECK (ttft_ms IS NULL OR total_ms IS NULL OR ttft_ms <= total_ms)
);

-- Both read paths filter on one conversation and order by id: the page that
-- shows a conversation, and the history sent to the model with each message.
-- Postgres does not index a foreign key on its own, so this also keeps the
-- ON DELETE CASCADE from scanning the whole table.
CREATE INDEX IF NOT EXISTS messages_conversation_id_idx
    ON messages (conversation_id, id);
