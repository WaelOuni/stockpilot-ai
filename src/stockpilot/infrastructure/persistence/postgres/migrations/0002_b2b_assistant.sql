-- Explicit B2B assistant transcript/action journal, no historical backfill.
CREATE TABLE assistant_turns (
 organization_id text NOT NULL, owner_user_id text NOT NULL, conversation_id text NOT NULL,
 turn_id text NOT NULL, fingerprint text NOT NULL,
 kind text NOT NULL CHECK (kind IN ('message','confirm','cancel')),
 input jsonb NOT NULL CHECK (octet_length(input::text) <= 20000),
 action jsonb CHECK (octet_length(action::text) <= 20000),
 reply jsonb CHECK (octet_length(reply::text) <= 65536),
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed')),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (organization_id, owner_user_id, conversation_id, turn_id),
 FOREIGN KEY (organization_id, owner_user_id, conversation_id) REFERENCES conversations,
 CHECK ((status='pending') OR reply IS NOT NULL)
);
CREATE INDEX assistant_turns_history ON assistant_turns
 (organization_id, owner_user_id, conversation_id, created_at DESC, turn_id DESC);
