#!/bin/bash
# Restore the Madden Database into the Docker Container
# Usage: ./restore_db.sh <backup_file.sql>

if [ -z "$1" ]; then
    echo "Error: Please provide the backup file you want to restore."
    echo "Usage: db/restore_db.sh db/backups/madden_backup_20340101_120000.sql"
    exit 1
fi

echo "Restoring database from $1..."

# Drop and recreate the database to ensure a perfectly clean slate
docker exec madden-db psql -U postgres -c "DROP DATABASE IF EXISTS madden WITH (FORCE);"
docker exec madden-db psql -U postgres -c "CREATE DATABASE madden;"

# Pipe the backup file into the fresh database
cat "$1" | docker exec -i madden-db psql -U postgres -d madden

echo "Restore complete!"
