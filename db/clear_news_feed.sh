#!/bin/bash
# Clear generated newsroom content (news_feed) from the Madden database, after backing it up.
#
#   bash db/clear_news_feed.sh                 # all articles AND tweets
#   bash db/clear_news_feed.sh --articles-only # articles only, keep tweets
#
# Deleting news_feed rows also removes their article_teams / article_players tags, any power_rankings rows
# parsed from those articles, and the agent takes and predictions recorded from them (ON DELETE CASCADE).
# Agent season summaries are built from those takes, so they are deleted too. The backup is written to
# db/backups/news_feed_backup_<timestamp>.sql first; restore it with:
#   docker exec -i madden-db psql -U postgres -d madden < db/backups/news_feed_backup_<timestamp>.sql
set -euo pipefail

CONTAINER=madden-db
PSQL=(docker exec -i "$CONTAINER" psql -U postgres -d madden -v ON_ERROR_STOP=1 -At)

WHERE="TRUE"
SCOPE="all articles and tweets"
if [[ "${1:-}" == "--articles-only" ]]; then
  WHERE="content_type = 'ARTICLE'"
  SCOPE="articles only (tweets kept)"
fi

echo "Current news_feed contents:"
"${PSQL[@]}" -F $'\t' -c "SELECT content_type, COUNT(*), MIN(season)||' W'||MIN(week), MAX(season)||' W'||MAX(week) FROM news_feed GROUP BY content_type ORDER BY 1;" \
  | awk -F'\t' '{printf "  %-8s %6s rows   (earliest %s, latest %s)\n", $1, $2, $3, $4}'
TOTAL=$("${PSQL[@]}" -c "SELECT COUNT(*) FROM news_feed WHERE $WHERE;")
if [[ "$TOTAL" == "0" ]]; then
  echo "Nothing to delete."
  exit 0
fi

# Back up every table the delete touches (power_rankings only if its migration has been applied).
TABLES=(-t news_feed -t article_teams -t article_players)
for T in power_rankings agent_memory_entries agent_predictions agent_season_summaries; do
  if [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.$T') IS NOT NULL;")" == "t" ]]; then
    TABLES+=(-t "$T")
  fi
done
BACKUP="$(dirname "$0")/backups/news_feed_backup_$(date +%Y%m%d_%H%M%S).sql"
mkdir -p "$(dirname "$BACKUP")"
docker exec "$CONTAINER" pg_dump -U postgres -d madden --data-only "${TABLES[@]}" > "$BACKUP"
echo "Backup written: $BACKUP"

read -r -p "Delete $TOTAL news_feed rows ($SCOPE)? Type DELETE to confirm: " CONFIRM
if [[ "$CONFIRM" != "DELETE" ]]; then
  echo "Cancelled. Nothing was deleted."
  exit 1
fi

"${PSQL[@]}" -c "DELETE FROM news_feed WHERE $WHERE;" >/dev/null
if [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.agent_season_summaries') IS NOT NULL;")" == "t" ]]; then
  "${PSQL[@]}" -c "DELETE FROM agent_season_summaries;" >/dev/null
  echo "Cleared agent season summaries."
fi
echo "Deleted $TOTAL rows. Remaining in news_feed: $("${PSQL[@]}" -c "SELECT COUNT(*) FROM news_feed;")"
