import { useEffect, useState, type CSSProperties } from "react";
import { API_BASE } from "../../api";
import { logo } from "../../lib/gridiron";

export function GamePage({ gameId }: { gameId: number }) {
  const [tab, setTab] = useState<"team" | "player">("team");
  const [gameData, setGameData] = useState<any>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/game/${gameId}`)
      .then(res => res.json())
      .then(data => setGameData(data));
  }, [gameId]);

  if (!gameData || gameData.error) return <main style={{ padding: "4rem", textAlign: "center" }}>Loading game...</main>;

  const { game } = gameData;
  const isFinal = game.status === "COMPLETED";
  const awayScore = game.away_score;
  const homeScore = game.home_score;
  const week = game.week;
  const season = game.season;
  
  const away = { name: game.away_name, abbr: game.away_abbr.toLowerCase() };
  const home = { name: game.home_name, abbr: game.home_abbr.toLowerCase() };

  const teamStats = gameData.team_stats || [];
  const awayStats = teamStats.find((t: any) => t.team_abbr.toLowerCase() === away.abbr.toLowerCase()) || {};
  const homeStats = teamStats.find((t: any) => t.team_abbr.toLowerCase() === home.abbr.toLowerCase()) || {};

  const statRows = [
    ["Total Yards", String(awayStats.total_offense || 0), String(homeStats.total_offense || 0)],
    ["Passing Yards", String(awayStats.passing_yards || 0), String(homeStats.passing_yards || 0)],
    ["Rushing Yards", String(awayStats.rushing_yards || 0), String(homeStats.rushing_yards || 0)],
    ["Passing Touchdowns", String(awayStats.passing_tds || 0), String(homeStats.passing_tds || 0)],
    ["Rushing Touchdowns", String(awayStats.rushing_tds || 0), String(homeStats.rushing_tds || 0)],
    ["Turnovers", String(awayStats.turnovers || 0), String(homeStats.turnovers || 0)],
  ];
  // Situational football and the line score: real values only (the API blanks estimated ones), so a row shows
  // only when at least one team has it recorded for this game
  const conv = (made: any, att: any) => (made === null || made === undefined ? null : att === null || att === undefined ? `${made}` : `${made}/${att}`);
  const redZone = (t: any) => (t.red_zone_tds === null || t.red_zone_tds === undefined ? null : `${t.red_zone_tds} TD · ${t.red_zone_fgs ?? 0} FG`);
  const penalties = (t: any) => (t.penalty_yards === null || t.penalty_yards === undefined ? null : t.penalties !== null && t.penalties !== undefined ? `${t.penalties}–${t.penalty_yards}` : `${t.penalty_yards} yds`);
  const clock = (sec: any) => (sec === null || sec === undefined ? null : `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`);
  const situationalRows = ([
    ["First Downs", awayStats.first_downs, homeStats.first_downs],
    ["Third Down", conv(awayStats.third_down_conversions, awayStats.third_down_attempts), conv(homeStats.third_down_conversions, homeStats.third_down_attempts)],
    ["Fourth Down", conv(awayStats.fourth_down_conversions, awayStats.fourth_down_attempts), conv(homeStats.fourth_down_conversions, homeStats.fourth_down_attempts)],
    ["Two-Point", conv(awayStats.two_point_conversions, awayStats.two_point_attempts), conv(homeStats.two_point_conversions, homeStats.two_point_attempts)],
    ["Red Zone", redZone(awayStats), redZone(homeStats)],
    ["Red Zone Trips", awayStats.red_zone_attempts, homeStats.red_zone_attempts],
    ["Penalties", penalties(awayStats), penalties(homeStats)],
    ["Time of Possession", clock(awayStats.time_of_possession_s), clock(homeStats.time_of_possession_s)],
  ] as [string, any, any][])
    .filter(([, a, h]) => (a !== null && a !== undefined) || (h !== null && h !== undefined))
    .map(([label, a, h]) => [label, a === null || a === undefined ? "—" : String(a), h === null || h === undefined ? "—" : String(h)]);
  const hasLineScore = awayStats.q1 !== null && awayStats.q1 !== undefined && homeStats.q1 !== null && homeStats.q1 !== undefined;
  const hasOvertime = hasLineScore && ((awayStats.ot || 0) > 0 || (homeStats.ot || 0) > 0);
  const rawStats = gameData.stats || [];
  
  const passingPlayers = rawStats
    .filter((p: any) => p.pass_yards >= 1)
    .sort((a: any, b: any) => b.pass_yards - a.pass_yards)
    .map((p: any) => ({
      id: p.player_id || p.id,
      name: p.player_name,
      abbr: p.team_abbr.toLowerCase(),
      stats: [`${p.completions}/${p.pass_attempts}`, String(p.pass_yards), String(p.pass_tds), String(p.interceptions)]
    }));

  const rushingPlayers = rawStats
    .filter((p: any) => p.rush_yards >= 1)
    .sort((a: any, b: any) => b.rush_yards - a.rush_yards)
    .map((p: any) => ({
      id: p.player_id || p.id,
      name: p.player_name,
      abbr: p.team_abbr.toLowerCase(),
      stats: [String(p.rush_attempts), String(p.rush_yards), p.rush_attempts > 0 ? (p.rush_yards / p.rush_attempts).toFixed(1) : "0.0", String(p.rush_tds)]
    }));

  const receivingPlayers = rawStats
    .filter((p: any) => p.rec_yards >= 1)
    .sort((a: any, b: any) => b.rec_yards - a.rec_yards)
    .map((p: any) => ({
      id: p.player_id || p.id,
      name: p.player_name,
      abbr: p.team_abbr.toLowerCase(),
      stats: [String(p.receptions), String(p.rec_yards), p.receptions > 0 ? (p.rec_yards / p.receptions).toFixed(1) : "0.0", String(p.rec_tds)]
    }));

  const num = (v: any) => (v === null || v === undefined ? "—" : String(v));
  const defensePlayers = (gameData.defense || []).map((p: any) => ({
    id: p.player_id, name: p.player_name, abbr: p.team_abbr.toLowerCase(),
    stats: [num(p.def_tackles), num(p.def_sacks), num(p.def_tfl), num(p.def_ints), num(p.def_deflections), num(p.def_forced_fumbles)],
  }));
  const kickingPlayers = (gameData.kicking || []).map((p: any) => ({
    id: p.player_id, name: p.player_name, abbr: p.team_abbr.toLowerCase(),
    stats: [`${p.kick_fgm ?? 0}/${p.kick_fga ?? 0}`, num(p.kick_fg_long), `${p.kick_xpm ?? 0}/${p.kick_xpa ?? 0}`],
  }));
  const puntingPlayers = (gameData.punting || []).map((p: any) => ({
    id: p.player_id, name: p.player_name, abbr: p.team_abbr.toLowerCase(),
    stats: [num(p.punt_punts), num(p.punt_average), num(p.punt_net_average), num(p.punt_inside_20)],
  }));

  const playerGroups = [
    {
      title: "Passing",
      labels: ["Player", "C/ATT", "YDS", "TD", "INT"],
      category: "passing",
      players: passingPlayers,
    },
    {
      title: "Rushing",
      labels: ["Player", "CAR", "YDS", "AVG", "TD"],
      category: "rushing",
      players: rushingPlayers,
    },
    {
      title: "Receiving",
      labels: ["Player", "REC", "YDS", "AVG", "TD"],
      category: "receiving",
      players: receivingPlayers,
    },
    // Defense and special teams appear only for games whose player stats were captured
    { title: "Defense", labels: ["Player", "TKL", "SCK", "TFL", "INT", "PD", "FF"], category: "defense", players: defensePlayers },
    { title: "Kicking", labels: ["Player", "FG", "LNG", "XP"], category: "kicking", players: kickingPlayers },
    { title: "Punting", labels: ["Player", "PUNTS", "AVG", "NET", "IN 20"], category: "punting", players: puntingPlayers },
  ].filter((group) => group.players.length > 0 || ["passing", "rushing", "receiving"].includes(group.category));

  let network = "GRIDIRON+";
  const gameTimeStr = game.game_time || "";
  if (gameTimeStr.toLowerCase().includes("thu")) network = "PRIME";
  else if (gameTimeStr.toLowerCase().includes("mon")) network = "ESPN";
  else if (gameTimeStr.toLowerCase().includes("sun")) {
    if (gameTimeStr.includes("8:") || gameTimeStr.includes("7:")) network = "NBC / PEACOCK";
    else network = game.id % 2 === 0 ? "CBS" : "FOX";
  }

  return (
    <main className="game-page">
      <section className="game-masthead">
        <div className="game-context">
          <span>{season} REGULAR SEASON · WEEK {week}</span>
          <strong>{isFinal ? `FINAL · ${game.game_time ? game.game_time.toUpperCase() : ''} ET` : game.game_time ? `${game.game_time.toUpperCase()} ET` : "TBD"}</strong>
        </div>
        <div className="game-scoreboard">
          <a href={`#team-${away.abbr}`} className="game-team away">
            <div><span>AWAY</span><h1>{away.name}</h1><small>{game.away_wins}-{game.away_losses}</small></div>
            <img src={logo(away.abbr)} alt={`${away.name} logo`} />
            <strong>{isFinal ? awayScore : "—"}</strong>
          </a>
          <div className="game-divider"><span>{isFinal ? "FINAL" : "VS"}</span><i /></div>
          <a href={`#team-${home.abbr}`} className="game-team">
            <strong>{isFinal ? homeScore : "—"}</strong>
            <img src={logo(home.abbr)} alt={`${home.name} logo`} />
            <div><span>HOME</span><h1>{home.name}</h1><small>{game.home_wins}-{game.home_losses}</small></div>
          </a>
        </div>
        <div className="game-location">{network} · {game.home_stadium ? game.home_stadium.toUpperCase() : "GRIDIRON FIELD"} · {home.name.toUpperCase()}</div>
      </section>

      <nav className="game-tabs" aria-label="Game statistics">
        <button className={tab === "team" ? "active" : ""} onClick={() => setTab("team")}>Team Stats</button>
        <button className={tab === "player" ? "active" : ""} onClick={() => setTab("player")}>Player Stats</button>
      </nav>

      {tab === "team" ? (
        <section className="game-stats-panel">
          <div className="game-stats-title"><span>Game Comparison</span><h2>Team Statistics</h2></div>
          {hasLineScore && (
            <table className="game-line-score">
              <thead>
                <tr><th>TEAM</th><th>1</th><th>2</th><th>3</th><th>4</th>{hasOvertime && <th>OT</th>}<th>T</th></tr>
              </thead>
              <tbody>
                {[[away, awayStats, awayScore], [home, homeStats, homeScore]].map(([team, t, total]: any) => (
                  <tr key={team.abbr}>
                    <td><img src={logo(team.abbr)} alt="" />{team.abbr.toUpperCase()}</td>
                    <td>{t.q1}</td><td>{t.q2}</td><td>{t.q3}</td><td>{t.q4}</td>{hasOvertime && <td>{t.ot ?? 0}</td>}<td><strong>{total}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="comparison-head">
            <span><img src={logo(away.abbr)} alt="" />{away.abbr.toUpperCase()}</span>
            <span>TEAM STATS</span>
            <span>{home.abbr.toUpperCase()}<img src={logo(home.abbr)} alt="" /></span>
          </div>
          <div className="comparison-table">
            {statRows.map((stat, index) => {
              const awayValue = Number.parseFloat(stat[1]);
              const homeValue = Number.parseFloat(stat[2]);
              return (
                <div className="comparison-row" key={stat[0]}>
                  <strong className={awayValue > homeValue ? "leader" : ""}>{stat[1]}</strong>
                  <span>{stat[0]}</span>
                  <strong className={homeValue > awayValue ? "leader" : ""}>{stat[2]}</strong>
                  {index < 5 && <i style={{ 
                    "--away-share": `${awayValue + homeValue > 0 ? (awayValue / (awayValue + homeValue)) * 100 : 50}%`,
                    "--away-color": game.away_color || "var(--red)",
                    "--home-color": game.home_color || "var(--gray-300)"
                  } as CSSProperties} />}
                </div>
              );
            })}
            {situationalRows.length > 0 && <div className="comparison-subhead">Situational</div>}
            {situationalRows.map((stat) => (
              <div className="comparison-row" key={stat[0]}>
                <strong>{stat[1]}</strong>
                <span>{stat[0]}</span>
                <strong>{stat[2]}</strong>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <section className="game-stats-panel">
          <div className="game-stats-title"><span>Game Leaders</span><h2>Player Statistics</h2></div>
          <div className="player-stat-groups">
            {playerGroups.map((group) => (
              <article className="player-stat-card" key={group.title}>
                <h3>{group.title}</h3>
                <div className="player-stat-row stat-labels" style={{ gridTemplateColumns: `2fr repeat(${group.labels.length - 1}, 1fr)` }}>
                  {group.labels.map((label) => <span key={label}>{label}</span>)}
                </div>
                <div className="player-stat-list" style={{ maxHeight: "270px", overflowY: "auto" }}>
                  {group.players.map((player: any) => (
                    <div className="player-stat-row" key={player.id} style={{ gridTemplateColumns: `2fr repeat(${group.labels.length - 1}, 1fr)` }}>
                      <span>
                        <a className="game-player-link" href={`#player-${player.id}`}>
                          <img src={logo(player.abbr)} alt="" />{player.name}
                        </a>
                      </span>
                      {player.stats.map((val: string, index: number) => (
                        <span key={index}>{val}</span>
                      ))}
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
