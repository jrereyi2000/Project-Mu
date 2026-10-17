export interface NapperUserProfile {
  id: number;
  handle: string;
  display_name: string;
  avatar_url: string;
  trophies: number;
}

export interface NapperLeagueSummary {
  id: number;
  name: string;
  season: number;
  teams_count: number;
  scoring_type: string;
  status: "PRE_DRAFT" | "IN_SEASON" | "COMPLETED";
  avatar_url: string;
  user_team_id: number;
  user_team_name: string;
  user_score?: number;
  user_proj?: number;
  opponent_team_name?: string;
  opponent_score?: number;
  opponent_proj?: number;
  unplayed_count?: number;
}

export interface NapperLeagueDetail {
  id: number;
  name: string;
  season: number;
  teams_count: number;
  scoring_type: string;
  roster_format: Record<string, number>;
  status: "PRE_DRAFT" | "IN_SEASON" | "COMPLETED";
  avatar_url: string;
  current_week: number;
}

export interface NapperTeam {
  id: number;
  league_id: number;
  user_id?: number | null;
  team_name: string;
  manager_handle: string;
  avatar_url: string;
  is_ai: boolean;
  persona?: "TRASH_TALKER" | "ANALYTICS_NERD" | "WAIVER_HAWK" | "DIE_HARD";
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  points_against: number;
  seed?: number;
}

export interface NapperStarterPlayer {
  id: number;
  player_id: number;
  name: string;
  position: string;
  team: string;
  opponent?: string;
  kickoff?: string;
  game_status?: string;
  headshot_url: string;
  fpts: number;
  proj_fpts: number;
  is_yet_to_play: boolean;
  injury_status?: string | null;
}

export interface NapperStarterSlot {
  slot: string; // QB, RB, WR, TE, FLEX, SUPERFLEX, BN, IR
  slot_label: string;
  left_player: NapperStarterPlayer | null;
  right_player: NapperStarterPlayer | null;
}

export interface NapperMatchupView {
  league_id: number;
  week: number;
  left_team: NapperTeam & {
    score: number;
    proj: number;
    win_prob: number;
    yet_to_play_summary: string;
  };
  right_team: NapperTeam & {
    score: number;
    proj: number;
    win_prob: number;
    yet_to_play_summary: string;
  };
  starters: NapperStarterSlot[];
  bench: NapperStarterSlot[];
}

export interface NapperChatMessage {
  id: number;
  league_id: number;
  sender_type: "USER" | "AI_MANAGER" | "SYSTEM_BOT";
  sender_name: string;
  sender_handle: string;
  avatar_url: string;
  message_text: string;
  card_payload?: {
    type: "BENCHWARMER" | "HIGH_SCORER" | "HEARTBREAKER" | "WAIVER_ALERT" | "TRADE_ALERT";
    title: string;
    subtitle: string;
    player?: {
      name: string;
      position: string;
      team: string;
      stats: string;
      fpts: number;
      headshot_url: string;
    };
  };
  created_at: string;
}

export interface NapperTradeOffer {
  id: number;
  league_id: number;
  week: number;
  proposer_team: NapperTeam;
  target_team: NapperTeam;
  offered_players: NapperStarterPlayer[];
  requested_players: NapperStarterPlayer[];
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "EXPIRED" | "COUNTERED";
  initiated_by_ai: boolean;
  ai_rationale?: string;
  created_at: string;
}

export interface NapperWaiverPlayer {
  player_id: number;
  name: string;
  position: string;
  team: string;
  opponent?: string;
  matchup_stars?: number;
  proj_fpts: number;
  avg_fpts: number;
  ros_rank?: string;
  rostered_pct: number;
  headshot_url: string;
}

export interface NapperDraftPlayer {
  player_id: number;
  name: string;
  position: "QB" | "RB" | "WR" | "TE" | "K" | "DEF" | string;
  team: string;
  bye: number;
  age: number;
  ros_pct: number;
  adp: number;
  adp_rank: number;
  proj_pts: number;
  proj_avg: number;
  rush_att?: number;
  rush_yds?: number;
  rush_td?: number;
  rec?: number;
  rec_yds?: number;
  rec_td?: number;
  pass_yds?: number;
  pass_td?: number;
  int?: number;
  headshot_url: string;
  is_drafted?: boolean;
  drafted_by_team_id?: number;
  draft_pick_num?: number;
  notes?: string;
}

export interface NapperDraftPick {
  pick_overall: number; // 1 to total_picks
  round: number; // 1 to rounds_count
  round_pick: number; // 1 to teams_count
  team_id: number;
  team_name: string;
  team_slot: number; // 1-indexed column index (1 to teams_count)
  player?: NapperDraftPlayer | null;
  timestamp?: string;
}

export interface NapperDraftTeam {
  id: number;
  slot: number; // 1 to teams_count
  name: string;
  is_user: boolean;
  is_ai: boolean;
  avatar_url: string;
  manager_handle: string;
  roster: NapperDraftPlayer[];
  grade?: string;
  proj_fpts_total?: number;
}

export interface NapperDraftComment {
  id: string;
  sender_name: string;
  sender_handle: string;
  avatar_url?: string;
  message: string;
  timestamp: string;
  is_bot?: boolean;
  pick_badge?: {
    pick_num: string;
    player_name: string;
    pos: string;
  };
}

export interface NapperDraftState {
  id: number | string;
  league_id?: number;
  title: string;
  teams_count: number;
  rounds_count: number;
  seconds_per_pick: number;
  status: "NOT_STARTED" | "IN_PROGRESS" | "PAUSED" | "COMPLETED";
  current_pick_index: number; // 0 to (teams_count * rounds_count - 1)
  user_slot: number; // 1 to teams_count
  auto_pick: boolean;
  teams: NapperDraftTeam[];
  picks: NapperDraftPick[];
  roster_slots: string[];
}
