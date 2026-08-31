#!/bin/bash
# Dumps the bot's Postgres database and prunes backups older than
# RETENTION_DAYS. Meant to run daily via cron on the host (not in a
# container) — see README section on backups for the crontab line.
set -euo pipefail

COMPOSE_DIR="/root/second.bot"
BACKUP_DIR="/root/pg_backups"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%F_%H%M)
FILE="$BACKUP_DIR/secondbot_${TIMESTAMP}.sql.gz"

cd "$COMPOSE_DIR"
docker compose exec -T db pg_dump -U postgres secondbot | gzip > "$FILE"

find "$BACKUP_DIR" -name "secondbot_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "Backup saved: $FILE"
