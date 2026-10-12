#!/bin/bash
# Backup the Madden Database from the Docker Container

echo "Backing up Madden Franchise database..."
docker exec madden-db pg_dump -U postgres -d madden > "$(dirname "$0")/backups/madden_backup_$(date +%Y%m%d_%H%M%S).sql"
echo "Backup complete! File saved in db/backups/."
