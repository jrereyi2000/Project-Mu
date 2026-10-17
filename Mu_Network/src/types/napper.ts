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
  persona?: "TRASH_TALKER" | "ANALYTICS_NERD" | "WAIVER_HAWK" | "DIE_HARD" | "ZERO_RB_ZEALOT" | "HERO_RB" | "TRADE_FIEND" | "UNDERDOG";
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
    type: "BENCHWARMER" | "HIGH_SCORER" | "HEARTBREAKER" | "WAIVER_ALERT" | "TRADE_ALERT" | "WAIVER_CLAIM";
    award_type?: string;
    player_name?: string;
    points?: number;
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
    details?: {
      offered?: string;
      requested?: string;
      rationale?: string;
      [key: string]: any;
    };
    [key: string]: any;
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
  persona?: string; // e.g. "USER" | "ZERO_RB_ZEALOT" | "HERO_RB" | "ANALYTICS_NERD" | "TRASH_TALKER" | "WAIVER_HAWK" | "DIE_HARD"
}

export type NapperManagerArchetype =
  | "ZERO_RB_ZEALOT"
  | "HERO_RB"
  | "ANALYTICS_NERD"
  | "TRASH_TALKER"
  | "WAIVER_HAWK"
  | "DIE_HARD"
  | "TRADE_FIEND"
  | "UNDERDOG";

export interface ArchetypeMeta {
  key: NapperManagerArchetype;
  label: string;
  shortLabel: string;
  tagline: string;
  description: string;
  color: string;
  bgColor: string;
  borderColor: string;
}

export const ARCHETYPE_METAS: Record<NapperManagerArchetype, ArchetypeMeta> = {
  ANALYTICS_NERD: {
    key: "ANALYTICS_NERD",
    label: "Analytics Nerd",
    shortLabel: "Analytics",
    tagline: "VORP & Market Inefficiencies",
    description: "Strict VORP optimizer. Hunts falling ADP values and market inefficiencies without emotion.",
    color: "#38bdf8",
    bgColor: "rgba(56, 189, 248, 0.15)",
    borderColor: "rgba(56, 189, 248, 0.4)",
  },
  ZERO_RB_ZEALOT: {
    key: "ZERO_RB_ZEALOT",
    label: "Zero-RB Zealot",
    shortLabel: "Zero-RB",
    tagline: "Pass-Catcher Stack / No Early RBs",
    description: "Hard constraint against RBs through round 5. Prioritizes elite WRs, top TEs, and dual-threat QBs.",
    color: "#a855f7",
    bgColor: "rgba(168, 85, 247, 0.15)",
    borderColor: "rgba(168, 85, 247, 0.4)",
  },
  HERO_RB: {
    key: "HERO_RB",
    label: "Hero RB",
    shortLabel: "Hero RB",
    tagline: "Bellcow Anchor & WR Avalanche",
    description: "Locks a tier-1 bellcow running back in round 1 or 2, then shifts heavily to pass-catchers.",
    color: "#10b981",
    bgColor: "rgba(16, 185, 129, 0.15)",
    borderColor: "rgba(16, 185, 129, 0.4)",
  },
  TRASH_TALKER: {
    key: "TRASH_TALKER",
    label: "Trash Talker",
    shortLabel: "Trash Talker",
    tagline: "Volatile Reaches & Maximum Ceiling",
    description: "High variance and swagger. Reaches ahead of ADP for explosive offensive weapons and taunts the room.",
    color: "#f43f5e",
    bgColor: "rgba(244, 63, 94, 0.15)",
    borderColor: "rgba(244, 63, 94, 0.4)",
  },
  WAIVER_HAWK: {
    key: "WAIVER_HAWK",
    label: "Waiver Hawk",
    shortLabel: "Waiver Hawk",
    tagline: "Balanced Touch Share & High Floor",
    description: "Builds deep, balanced lineups with proven touch shares, setting up versatile weekly management.",
    color: "#00ceb8",
    bgColor: "rgba(0, 206, 184, 0.15)",
    borderColor: "rgba(0, 206, 184, 0.4)",
  },
  DIE_HARD: {
    key: "DIE_HARD",
    label: "Die-Hard Fan",
    shortLabel: "Die-Hard",
    tagline: "Proven Veterans & Franchise Grit",
    description: "Values high-floor proven leaders and franchise cornerstones, undervaluing unproven rookies.",
    color: "#f59e0b",
    bgColor: "rgba(245, 158, 11, 0.15)",
    borderColor: "rgba(245, 158, 11, 0.4)",
  },
  TRADE_FIEND: {
    key: "TRADE_FIEND",
    label: "Trade Fiend",
    shortLabel: "Trade Fiend",
    tagline: "Aggressive Deals & Buy-Low / Sell-High",
    description: "Relentless dealmaker viewing fantasy as an asset market. Consolidates depth and hunts 2-for-1 upgrades.",
    color: "#ec4899",
    bgColor: "rgba(236, 72, 153, 0.15)",
    borderColor: "rgba(236, 72, 153, 0.4)",
  },
  UNDERDOG: {
    key: "UNDERDOG",
    label: "The Underdog",
    shortLabel: "Underdog",
    tagline: "Young Sleepers & High-Ceiling Breakouts",
    description: "Chases high-upside younger talent, rookie breakout candidates, and sleeper prospects for league-winning ceiling.",
    color: "#6366f1",
    bgColor: "rgba(99, 102, 241, 0.15)",
    borderColor: "rgba(99, 102, 241, 0.4)",
  },
};

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
