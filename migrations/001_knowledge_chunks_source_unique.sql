-- PostgreSQL 15+; the project uses PostgreSQL 16.
-- Check for duplicates before applying; this migration intentionally deletes nothing.
ALTER TABLE knowledge_chunks
ADD CONSTRAINT knowledge_chunks_source_unique
UNIQUE NULLS NOT DISTINCT (source);
