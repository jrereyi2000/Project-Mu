#!/bin/bash
# Backup a Madden database from the Docker Container: MU_DB if set, else the active franchise (db/franchises.py).
#   bash db/backup_db.sh                  # active franchise
#   MU_DB=madden_2036 bash db/backup_db.sh

DB="${MU_DB:-$(cd "$(dirname "$0")/.." && python3 -m db.franchises dbname)}" || exit 1
echo "Backing up database $DB..."
docker exec madden-db pg_dump -U postgres -d "$DB" > "$(dirname "$0")/backups/${DB}_backup_$(date +%Y%m%d_%H%M%S).sql"
echo "Backup complete! File saved in db/backups/."
