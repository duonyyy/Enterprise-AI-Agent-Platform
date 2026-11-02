#!/bin/sh
set -eu

: "${PLATFORM_APP_DB_PASSWORD:?PLATFORM_APP_DB_PASSWORD must be supplied by the deployment secret store}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=platform_app_password="$PLATFORM_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE platform_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  PASSWORD :'platform_app_password';
SQL
