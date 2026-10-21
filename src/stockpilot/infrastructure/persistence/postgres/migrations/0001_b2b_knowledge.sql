-- B2B only. search_path is set by the explicit migration runner.
CREATE TABLE documents (
 organization_id text NOT NULL, document_id text NOT NULL, title text NOT NULL,
 revision bigint NOT NULL CHECK (revision >= 0), active_generation text,
 status text NOT NULL CHECK (status IN ('pending','ready','failed','deleted')),
 embedding_model text NOT NULL, embedding_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, document_id)
);
CREATE TABLE ingestions (
 organization_id text NOT NULL, operation_id text NOT NULL, document_id text NOT NULL,
 actor_user_id text NOT NULL, fingerprint text NOT NULL, generation_id text NOT NULL,
 status text NOT NULL CHECK (status IN ('pending','ready','failed')),
 revision bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, operation_id),
 UNIQUE (organization_id, document_id, generation_id),
 FOREIGN KEY (organization_id, document_id) REFERENCES documents
);
CREATE TABLE chunks (
 organization_id text NOT NULL, document_id text NOT NULL, generation_id text NOT NULL,
 chunk_id integer NOT NULL CHECK (chunk_id >= 0), content text NOT NULL,
 embedding vector(768) NOT NULL,
 PRIMARY KEY (organization_id, document_id, generation_id, chunk_id),
 FOREIGN KEY (organization_id, document_id, generation_id)
 REFERENCES ingestions (organization_id, document_id, generation_id)
);
CREATE TABLE document_deletions (
 organization_id text NOT NULL, operation_id text NOT NULL, document_id text NOT NULL,
 actor_user_id text NOT NULL, fingerprint text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, operation_id)
);
CREATE TABLE conversations (
 organization_id text NOT NULL, owner_user_id text NOT NULL, conversation_id text NOT NULL,
 external_thread_id text NOT NULL, status text NOT NULL DEFAULT 'active'
 CHECK (status IN ('active','deleted')), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, owner_user_id, conversation_id),
 UNIQUE (organization_id, owner_user_id, external_thread_id)
);
CREATE TABLE checkpoints (
 organization_id text NOT NULL, owner_user_id text NOT NULL, conversation_id text NOT NULL,
 checkpoint_id text NOT NULL, parent_id text, checkpoint_type text NOT NULL,
 checkpoint bytea NOT NULL CHECK (octet_length(checkpoint) <= 1048576),
 metadata_type text NOT NULL, metadata bytea NOT NULL,
 PRIMARY KEY (organization_id, owner_user_id, conversation_id, checkpoint_id),
 FOREIGN KEY (organization_id, owner_user_id, conversation_id) REFERENCES conversations
);
CREATE TABLE checkpoint_writes (
 organization_id text NOT NULL, owner_user_id text NOT NULL, conversation_id text NOT NULL,
 checkpoint_id text NOT NULL, task_id text NOT NULL, idx integer NOT NULL,
 channel text NOT NULL, value_type text NOT NULL, value bytea NOT NULL
 CHECK (octet_length(value) <= 1048576),
 PRIMARY KEY (organization_id, owner_user_id, conversation_id, checkpoint_id, task_id, idx),
 FOREIGN KEY (organization_id, owner_user_id, conversation_id)
 REFERENCES conversations
);
