#!/bin/bash
# Clear generated newsroom content (news_feed) from the Madden database, after backing it up.
#
#   bash db/clear_news_feed.sh                 # all articles AND tweets
#   bash db/clear_news_feed.sh --articles-only # articles only, keep tweets
#
# Deleting news_feed rows also removes their article_teams / article_players tags, any power_rankings rows
# parsed from those articles, and the agent takes and predictions recorded from them (ON DELETE CASCADE).
# Agent season summaries are built from those takes, so they are deleted too, and Agent Wire trends
# (social_trends) are cleared whenever posts are deleted. Replies cascade with their parent post. The backup is written to
# db/backups/news_feed_backup_<timestamp>.sql first; restore it with:
#   docker exec -i madden-db psql -U postgres -d madden < db/backups/news_feed_backup_<timestamp>.sql
set -euo pipefail

CONTAINER=madden-db
PSQL=(docker exec -i "$CONTAINER" psql -U postgres -d madden -v ON_ERROR_STOP=1 -At)

ARTICLES_ONLY=false
SEASON=""
WEEK=""
CONFIRM_YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --articles-only)
      ARTICLES_ONLY=true
      shift
      ;;
    --week)
      WEEK="${2:-}"
      shift 2
      ;;
    --season)
      SEASON="${2:-}"
      shift 2
      ;;
    -y|--yes)
      CONFIRM_YES=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--week N] [--season YYYY] [--articles-only] [-y|--yes]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--week N] [--season YYYY] [--articles-only] [-y|--yes]"
      exit 1
      ;;
  esac
done

WHERE_PARTS=()
SCOPE_PARTS=()
if [ "$ARTICLES_ONLY" = true ]; then
  WHERE_PARTS+=("content_type = 'ARTICLE'")
  SCOPE_PARTS+=("articles only (tweets kept)")
else
  SCOPE_PARTS+=("articles and tweets")
fi

if [ -n "$SEASON" ]; then
  WHERE_PARTS+=("season = $SEASON")
  SCOPE_PARTS+=("season $SEASON")
fi

if [ -n "$WEEK" ]; then
  WHERE_PARTS+=("week = $WEEK")
  SCOPE_PARTS+=("week $WEEK")
fi

WHERE="TRUE"
if [ ${#WHERE_PARTS[@]} -gt 0 ]; then
  WHERE=$(printf " AND %s" "${WHERE_PARTS[@]}")
  WHERE="${WHERE:5}"
fi
SCOPE=""
if [ ${#SCOPE_PARTS[@]} -gt 0 ]; then
  SCOPE=$(printf ", %s" "${SCOPE_PARTS[@]}")
  SCOPE="${SCOPE:2}"
fi

echo "Current news_feed contents:"
"${PSQL[@]}" -F $'\t' -c "SELECT content_type, COUNT(*), MIN(season)||' W'||MIN(week), MAX(season)||' W'||MAX(week) FROM news_feed GROUP BY content_type ORDER BY 1;" \
  | awk -F'\t' '{printf "  %-8s %6s rows   (earliest %s, latest %s)\n", $1, $2, $3, $4}'
TOTAL=$("${PSQL[@]}" -c "SELECT COUNT(*) FROM news_feed WHERE $WHERE;")
if [[ "$TOTAL" == "0" ]]; then
  echo "Nothing to delete matching $WHERE."
  exit 0
fi

# Back up every table the delete touches (power_rankings only if its migration has been applied).
TABLES=(-t news_feed -t article_teams -t article_players)
for T in power_rankings agent_memory_entries agent_predictions agent_season_summaries social_trends; do
  if [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.$T') IS NOT NULL;")" == "t" ]]; then
    TABLES+=(-t "$T")
  fi
done
BACKUP="$(dirname "$0")/backups/news_feed_backup_$(date +%Y%m%d_%H%M%S).sql"
mkdir -p "$(dirname "$BACKUP")"
docker exec "$CONTAINER" pg_dump -U postgres -d madden --data-only "${TABLES[@]}" > "$BACKUP"
echo "Backup written: $BACKUP"

if [ "$CONFIRM_YES" = false ]; then
  read -r -p "Delete $TOTAL news_feed rows ($SCOPE)? Type DELETE to confirm: " CONFIRM
  if [[ "$CONFIRM" != "DELETE" ]]; then
    echo "Cancelled. Nothing was deleted."
    exit 1
  fi
fi

"${PSQL[@]}" -c "DELETE FROM news_feed WHERE $WHERE;" >/dev/null

if [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.agent_season_summaries') IS NOT NULL;")" == "t" ]]; then
  SUM_WHERE="TRUE"
  if [ -n "$SEASON" ] && [ -n "$WEEK" ]; then
    SUM_WHERE="season = $SEASON AND through_week >= $WEEK"
  elif [ -n "$SEASON" ]; then
    SUM_WHERE="season = $SEASON"
  elif [ -n "$WEEK" ]; then
    SUM_WHERE="through_week >= $WEEK"
  fi
  "${PSQL[@]}" -c "DELETE FROM agent_season_summaries WHERE $SUM_WHERE;" >/dev/null
  echo "Cleared agent season summaries ($SUM_WHERE)."
fi

# Agent Wire trends are computed from the posts; clear them whenever posts are deleted.
if [ "$ARTICLES_ONLY" = false ]; then
  if [[ "$("${PSQL[@]}" -c "SELECT to_regclass('public.social_trends') IS NOT NULL;")" == "t" ]]; then
    TREND_WHERE="TRUE"
    if [ -n "$SEASON" ] && [ -n "$WEEK" ]; then
      TREND_WHERE="season = $SEASON AND week = $WEEK"
    elif [ -n "$SEASON" ]; then
      TREND_WHERE="season = $SEASON"
    elif [ -n "$WEEK" ]; then
      TREND_WHERE="week = $WEEK"
    fi
    "${PSQL[@]}" -c "DELETE FROM social_trends WHERE $TREND_WHERE;" >/dev/null
    echo "Cleared Agent Wire trends ($TREND_WHERE)."
  fi
fi
echo "Deleted $TOTAL rows. Remaining in news_feed: $("${PSQL[@]}" -c "SELECT COUNT(*) FROM news_feed;")"
