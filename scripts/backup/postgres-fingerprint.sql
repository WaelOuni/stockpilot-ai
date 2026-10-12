-- Read-only, private output: all application schemas, table data and index definitions.
-- Same exact PostgreSQL image/settings must be used for comparison.
BEGIN READ ONLY;
SELECT format(
  'SELECT %L, count(*), encode(sha256(convert_to(COALESCE(string_agg(row_to_json(t)::text, E''\n'' ORDER BY row_to_json(t)::text), ''''), ''UTF8'')), ''hex'') FROM %I.%I t;',
  schemaname || '.' || tablename, schemaname, tablename)
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
  AND schemaname NOT LIKE 'pg_toast%'
ORDER BY schemaname, tablename
\gexec
SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schemaname, tablename, indexname;
SELECT schemaname, sequencename, sequenceowner, data_type, start_value,
       min_value, max_value, increment_by, cycle, last_value
FROM pg_sequences WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schemaname, sequencename;
SELECT datname, pg_get_userbyid(datdba), encoding, datcollate, datctype, datacl
FROM pg_database WHERE datname = current_database();
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT nspname, pg_get_userbyid(nspowner), nspacl FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY nspname;
SELECT schemaname, tablename, tableowner FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
  AND schemaname NOT LIKE 'pg_toast%' ORDER BY schemaname, tablename;
SELECT n.nspname, c.relname, c.relacl FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
ORDER BY n.nspname, c.relname;
SELECT n.nspname, r.relname, c.conname, pg_get_constraintdef(c.oid)
FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid
JOIN pg_namespace n ON n.oid = r.relnamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
ORDER BY n.nspname, r.relname, c.conname;
COMMIT;
