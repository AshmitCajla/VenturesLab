#!/usr/bin/env bash
# Consistent SQLite backup of the running stack, keeping the last 14 days.
# Cron (daily 03:00):  0 3 * * * cd /opt/ventureslab && ./scripts/backup.sh >> backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# sqlite3's online backup API is safe while the API is writing (WAL mode).
$compose exec -T backend python -c "
import sqlite3
src = sqlite3.connect('/app/data/ventureslab.db')
dst = sqlite3.connect('/app/data/backup.db')
src.backup(dst)
dst.close()
"
$compose cp backend:/app/data/backup.db "backups/ventureslab-$stamp.db"
gzip -f "backups/ventureslab-$stamp.db"
find backups -name 'ventureslab-*.db.gz' -mtime +14 -delete
echo "$(date -Is) backup ok: backups/ventureslab-$stamp.db.gz"
