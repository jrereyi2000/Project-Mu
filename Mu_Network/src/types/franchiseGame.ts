/**
 * JSON contract for the #franchise shell and Game Center (V2_CORE_BUILD_PLAN §2.7, §2.9, U17), mirroring the module
 * docstring of `server/franchise_api.py` and `pipeline/game_launch.py`:
 *
 *   GET  /api/franchise/{abbr}/overview      -> FranchiseOverview
 *   GET  /api/franchise/{abbr}/game-center   -> GameCenterResponse
 *   POST /api/franchise/{abbr}/game/launch   GameLaunchRequest -> GameLaunchResponse
 *
 * Same conventions as types/franchiseRoster.ts: money in whole dollars, `null` = not recorded (shown as "—"),
 * errors are FastAPI `{"detail": string}` (403 = not one of the active franchise's user teams, 404 = unknown team or
 * the API isn't mounted).
 */
import type { MoveKind, MoveStatus } from "./franchiseRoster";

// ---- Shared -------------------------------------------------------------------------------------------------

export type TeamRef = { id: number; abbr: string; name: string };

/** W-L-T over the season's completed regular-season games. */
export type TeamRecord = { wins: number; losses: number; ties: number };

/** The team's next unplayed game (week >= league_state's week, not COMPLETED or BYE). */
export type NextGame = {
  game_id: number;
  season: number;
  week: number;
  game_time: string | null;            // e.g. "Sun 1:00 PM"
  game_type: string | null;            // REGULAR_SEASON, WILDCARD, DIVISIONAL, CONFERENCE, SUPER_BOWL, ...
  home: boolean;
  /** abbr / name are null only when the opponent id has no teams row. */
  opponent: { id: number; abbr: string | null; name: string | null };
};

// ---- GET /{abbr}/overview -----------------------------------------------------------------------------------

/** A PENDING franchise_pending_moves row with its stored payload. */
export type PendingMove = {
  move_id: number;
  kind: MoveKind | "DEPTH_CHART";
  status: MoveStatus;
  created_at: string;                  // ISO timestamp
  player_id: number | null;            // null for DEPTH_CHART
  payload: Record<string, unknown>;
};

export type OverviewCap = {
  cap: number;
  committed: number;
  dead_money: number;
  rookie_reserve: number;
  room: number;                        // cap - committed - dead_money - rookie_reserve
  estimated: boolean;
};

export type FranchiseOverview = {
  team: TeamRef;
  season: number;
  week: number;
  league_year: number;                 // season + 1 from week 24 (the offseason), else season
  user_controlled: boolean;            // in the active franchise's user_teams (or that list is empty)
  record: TeamRecord;
  cap: OverviewCap;                    // at the league year
  roster_counts: {
    active: number;
    practice_squad: number;
    injured_reserve: number;
    limits: { active: number; practice_squad: number };
  };
  next_game: NextGame | null;
  pending_moves: PendingMove[];
};

// ---- GET /{abbr}/game-center --------------------------------------------------------------------------------

/** team_seasons ratings (ranks: 1 = best). */
export type TeamRatings = {
  team_ovr: number | null;
  off_ovr: number | null;
  def_ovr: number | null;
  pass_off_rank: number | null;
  rush_off_rank: number | null;
  pass_rush_rank: number | null;
  coverage_rank: number | null;
};

export const RANK_STATS = [
  "points_for", "points_against", "yards_for", "yards_against", "pass_yards_for", "pass_yards_against",
  "rush_yards_for", "rush_yards_against", "turnovers", "takeaways",
] as const;
export type RankStat = (typeof RANK_STATS)[number];

/** Per game value and league rank (1 = best of the teams with games; ties share a rank). */
export type RankCell = { value: number | null; rank: number | null };

export type Ranks = { games: number } & Record<RankStat, RankCell>;

export type MatchupEdge = "team" | "opponent" | "even";

export type Matchup = {
  unit: string;                        // "Pass offense v pass defense", "Opponent pass offense v pass defense", ...
  team_stat: RankStat;
  team_value: number | null;
  team_rank: number | null;
  opponent_stat: RankStat;
  opponent_value: number | null;
  opponent_rank: number | null;
  edge: MatchupEdge;                   // the better rank by more than 3 places, else even
};

export type GameCenterOpponent = TeamRef & { record: TeamRecord; ratings: TeamRatings | null };

export type GameCenterResponse = {
  team: string;                        // abbr
  season: number;
  week: number;
  record: TeamRecord;
  next_game: NextGame | null;
  opponent: GameCenterOpponent | null;
  ratings: TeamRatings | null;
  ranks: { team: Ranks | null; opponent: Ranks | null };
  matchups: Matchup[];                 // empty without ranks for both teams
};

// ---- POST /{abbr}/game/launch -------------------------------------------------------------------------------

export type GameLaunchRequest = { dry_run?: boolean; season?: number; week?: number };

/** The game pipeline/game_launch.py launched (home / away are abbreviations). */
export type LaunchedGame = {
  id: number;
  season: number;
  week: number;
  home: string;
  away: string;
  game_time: string;
  game_type: string;
};

export type GameLaunchResponse = {
  pack: string | null;                 // the mod pack's name, null when refused before it was chosen
  command: string | null;              // the cmd.exe line run (or, on a dry run, that would run)
  started: boolean;                    // Madden was started (never on a dry run)
  refused: string | null;              // why it refused: pending moves, Madden running, no single game, ...
  game: LaunchedGame | null;
};
