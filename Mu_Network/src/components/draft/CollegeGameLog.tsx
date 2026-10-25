import React from "react";

export interface GameLogEntry {
  step: number;
  college_week?: number | null;
  game_type: string;
  label: string;
  opponent: string;
  verdict: "SHINES" | "STEADY" | "STRUGGLES" | string;
  headline?: string;
  blurb?: string;
  stats: Record<string, number>;
  stat_line: string;
}

export interface SeasonStatsBlock {
  stat_group: string;
  stat_keys: string[];
  games: number;
  totals: Record<string, number>;
  summary_line: string;
}

const STAT_LABELS: Record<string, string> = {
  games: "GP",
  comp: "COMP",
  att: "ATT",
  comp_pct: "COMP %",
  pass_yds: "PASS YDS",
  ypa: "YPA",
  pass_td: "PASS TD",
  int: "INT",
  passer_rating: "RTG",
  rush_yds: "RUSH YDS",
  carries: "CAR",
  ypc: "YPC",
  rush_td: "RUSH TD",
  rec: "REC",
  targets: "TGT",
  catch_pct: "CATCH %",
  rec_yds: "REC YDS",
  ypr: "YPR",
  rec_td: "REC TD",
  total_yds: "TOT YDS",
  snaps: "SNAPS",
  sacks_allowed: "SACKS ALW",
  pressures_allowed: "PRESS ALW",
  penalties: "PEN",
  pass_block_efficiency: "PBE %",
  tackles: "TKL",
  tfl: "TFL",
  sacks: "SACKS",
  pressures: "PRESS",
  pbu: "PBU",
  catches_allowed: "CAT ALW",
  comp_pct_allowed: "ALW %",
  fgm: "FGM",
  fga: "FGA",
  fg_pct: "FG %",
  fg_long: "LONG",
  xpm: "XPM",
  punts: "PUNTS",
  punt_avg: "AVG",
  inside_20: "IN 20",
  bad_snaps: "BAD SNAPS",
};

function summaryOrderForGroup(group: string): string[] {
  switch (group) {
    case "QB":
      return ["games", "comp", "att", "comp_pct", "pass_yds", "ypa", "pass_td", "int", "passer_rating", "rush_yds"];
    case "RB":
      return ["games", "carries", "rush_yds", "ypc", "rush_td", "rec", "rec_yds", "total_yds"];
    case "WR":
    case "TE":
      return ["games", "rec", "targets", "catch_pct", "rec_yds", "ypr", "rec_td"];
    case "OT":
    case "IOL":
      return ["games", "snaps", "sacks_allowed", "pressures_allowed", "penalties", "pass_block_efficiency"];
    case "EDGE":
    case "DT":
      return ["games", "tackles", "tfl", "sacks", "pressures"];
    case "LB":
      return ["games", "tackles", "tfl", "sacks", "pbu"];
    case "CB":
      return ["games", "targets", "catches_allowed", "comp_pct_allowed", "pbu", "int"];
    case "S":
      return ["games", "tackles", "pbu", "int"];
    case "K":
      return ["games", "fgm", "fga", "fg_pct", "fg_long", "xpm"];
    case "P":
      return ["games", "punts", "punt_avg", "inside_20"];
    default:
      return ["games", "snaps", "bad_snaps"];
  }
}

export const CollegeGameLog: React.FC<{
  seasonStats: SeasonStatsBlock;
  gameLog: GameLogEntry[];
}> = ({ seasonStats, gameLog }) => {
  const { stat_group, stat_keys, totals, games } = seasonStats;
  const boxKeys = summaryOrderForGroup(stat_group).filter((k) => totals[k] !== undefined);

  if (!games || gameLog.length === 0) {
    return (
      <div className="draft-section-card" style={{ textAlign: "center", padding: "32px 20px", color: "#64748b" }}>
        No individual college game box scores charted for this prospect yet.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
      {/* Cumulative Season Totals Strip */}
      <div>
        <div style={{ fontSize: "11px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.06em", color: "#64748b", marginBottom: "8px" }}>
          Cumulative College Season Totals ({games} Charted Games)
        </div>
        <div className="draft-stat-boxes">
          {boxKeys.map((k) => {
            const val = totals[k];
            const isPct = k.includes("pct") || k === "pass_block_efficiency";
            return (
              <div key={k} className="draft-stat-box">
                <div className="draft-stat-box-label">{STAT_LABELS[k] || k.toUpperCase()}</div>
                <div className="draft-stat-box-val">
                  {typeof val === "number" ? (isPct ? `${val}%` : val.toLocaleString()) : val}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Game-by-Game Box Score Table */}
      <div className="draft-table-card">
        <div className="draft-table-scroll">
          <table className="draft-board-table">
            <thead>
              <tr>
                <th>Week / Stage</th>
                <th>Opponent</th>
                <th>Scout Verdict</th>
                {stat_keys.map((k) => (
                  <th key={k} style={{ textAlign: "right" }}>
                    {STAT_LABELS[k] || k.toUpperCase()}
                  </th>
                ))}
                <th>Film Note</th>
              </tr>
            </thead>
            <tbody>
              {gameLog.map((g, idx) => (
                <tr key={`${g.step}-${idx}`} style={{ cursor: "default" }}>
                  <td style={{ whiteSpace: "nowrap", fontWeight: 700, color: "#0f172a" }}>
                    <span style={{ textTransform: "capitalize" }}>{g.label}</span>
                    {g.game_type !== "REGULAR" && (
                      <span
                        style={{
                          marginLeft: "6px",
                          fontSize: "10px",
                          fontWeight: 800,
                          background: "#fef3c7",
                          color: "#92400e",
                          padding: "2px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        {g.game_type}
                      </span>
                    )}
                  </td>
                  <td style={{ fontWeight: 700, color: "#1e293b", whiteSpace: "nowrap" }}>vs. {g.opponent}</td>
                  <td>
                    <span className={`verdict-pill ${g.verdict}`}>{g.verdict}</span>
                  </td>
                  {stat_keys.map((k) => (
                    <td
                      key={k}
                      style={{
                        textAlign: "right",
                        fontFamily: "monospace",
                        fontWeight: 700,
                        fontSize: "13px",
                        color: "#0f172a",
                      }}
                    >
                      {g.stats[k] ?? 0}
                    </td>
                  ))}
                  <td style={{ fontSize: "12px", color: "#475569", maxWidth: "280px" }}>
                    {g.headline || g.stat_line}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default CollegeGameLog;
