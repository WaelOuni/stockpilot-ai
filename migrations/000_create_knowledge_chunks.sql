CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT,
    embedding VECTOR(768) NOT NULL
);
