#!/bin/bash
# Restore a backup into a Madden database in the Docker Container: MU_DB if set, else the active franchise
# (db/franchises.py). The target database is dropped and recreated first.
# Usage: ./restore_db.sh <backup_file.sql>
#        MU_DB=madden_2036 ./restore_db.sh <backup_file.sql>

if [ -z "$1" ]; then
    echo "Error: Please provide the backup file you want to restore."
    echo "Usage: db/restore_db.sh db/backups/madden_colts_backup_20340101_120000.sql"
    exit 1
fi

DB="${MU_DB:-$(cd "$(dirname "$0")/.." && python3 -m db.franchises dbname)}" || exit 1
echo "Restoring database $DB from $1..."

# Drop and recreate the database to ensure a perfectly clean slate
docker exec madden-db psql -U postgres -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE);"
docker exec madden-db psql -U postgres -c "CREATE DATABASE \"$DB\";"

# Pipe the backup file into the fresh database
cat "$1" | docker exec -i madden-db psql -U postgres -d "$DB"

echo "Restore complete!"
