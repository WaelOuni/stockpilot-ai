-- Private read-only output. Hashes verifiers instead of exposing them.
BEGIN READ ONLY;
SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin,
       rolreplication, rolconnlimit, rolvaliduntil, rolbypassrls,
       encode(sha256(convert_to(COALESCE(rolpassword, ''), 'UTF8')), 'hex')
FROM pg_authid WHERE rolname <> 'v112_restore_operator' ORDER BY rolname;
SELECT pg_get_userbyid(roleid), pg_get_userbyid(member), admin_option
FROM pg_auth_members
WHERE pg_get_userbyid(roleid) <> 'v112_restore_operator'
  AND pg_get_userbyid(member) <> 'v112_restore_operator'
ORDER BY 1, 2;
SELECT spcname, pg_get_userbyid(spcowner), spcacl, pg_tablespace_location(oid)
FROM pg_tablespace ORDER BY spcname;
COMMIT;
