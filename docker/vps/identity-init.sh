#!/bin/sh
set -eu
KC_PASSWORD=$(cat /run/kc-password)
export KC_PASSWORD
psql -v ON_ERROR_STOP=1 -U postgres -d keycloak <<'SQL'
\getenv password KC_PASSWORD
CREATE ROLE keycloak LOGIN PASSWORD :'password';
ALTER DATABASE keycloak OWNER TO keycloak;
SQL
