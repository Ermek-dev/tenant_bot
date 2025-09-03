#!/bin/sh
set -euo pipefail

DB_PATH="${DB_PATH:-/data/bot.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
INTERVAL="${BACKUP_INTERVAL_SEC:-3600}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

log() {
  echo "[backup] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
}

do_backup() {
  if [ ! -f "$DB_PATH" ]; then
    log "Database not found at $DB_PATH, skipping backup"
    return 0
  fi
  TS=$(date -u '+%Y%m%dT%H%M%SZ')
  OUT="$BACKUP_DIR/bot-$TS.db"
  # Use sqlite online backup for consistency with WAL
  log "Starting backup to $OUT"
  sqlite3 "$DB_PATH" ".backup '$OUT'" || {
    log "sqlite3 backup failed"
    return 1
  }
  # Optional gzip compression: uncomment if needed
  # gzip -9 "$OUT" && OUT="$OUT.gz"
  log "Backup completed: $OUT"

  # Retention
  if [ -n "$KEEP_DAYS" ]; then
    find "$BACKUP_DIR" -type f -name 'bot-*.db' -mtime +"$KEEP_DAYS" -print -delete || true
  fi
}

log "Backup loop started. DB_PATH=$DB_PATH, BACKUP_DIR=$BACKUP_DIR, INTERVAL=${INTERVAL}s, KEEP_DAYS=$KEEP_DAYS"

# Initial backup attempt
do_backup || true

while true; do
  sleep "$INTERVAL"
  do_backup || true
done

