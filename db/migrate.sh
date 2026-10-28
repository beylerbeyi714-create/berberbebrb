#!/bin/sh
# Her başlangıçta çalışır (docker-compose: db-init). İdempotenttir:
#   - n8n ve factory kullanıcılarını/veritabanlarını yoksa oluşturur
#   - şifrelerini .env'deki değerlerle eşitler (.env yeniden üretildiyse bağlantı kopmaz)
#   - factory şemasını uygular (tüm ifadeler IF NOT EXISTS / ON CONFLICT)
set -eu
H=postgres
until pg_isready -q -h "$H" -U postgres; do sleep 1; done

psql -q -h "$H" -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -v n8n_pw="$N8N_DB_PASSWORD" -v factory_pw="$FACTORY_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE n8n LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n') \gexec
SELECT 'CREATE ROLE factory LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'factory') \gexec
ALTER ROLE n8n WITH LOGIN PASSWORD :'n8n_pw';
ALTER ROLE factory WITH LOGIN PASSWORD :'factory_pw';
SELECT 'CREATE DATABASE n8n OWNER n8n' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'n8n') \gexec
SELECT 'CREATE DATABASE factory OWNER factory' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'factory') \gexec
SQL

PGPASSWORD="$FACTORY_DB_PASSWORD" PGOPTIONS='-c client_min_messages=warning' \
  psql -q -h "$H" -U factory -d factory -v ON_ERROR_STOP=1 -f /db/001_schema.sql
echo "veritabanı hazır"
