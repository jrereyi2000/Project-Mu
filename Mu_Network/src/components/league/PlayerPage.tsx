import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { Chevron, DEFAULT_AVATAR, getPlayerHeadshot, handleHeadshotError, logo } from "../../lib/gridiron";
import PlayerHonors from "../history/PlayerHonors";
import PlayerCareerPath from "./PlayerCareerPath";

export const POSITION_LABELS: Record<string, string> = {
  QB: "Quarterback", HB: "Halfback", FB: "Fullback", WR: "Wide Receiver", TE: "Tight End",
  LT: "Left Tackle", LG: "Left Guard", C: "Center", RG: "Right Guard", RT: "Right Tackle",
  LEDG: "Edge Rusher", REDG: "Edge Rusher", DT: "Defensive Tackle", MIKE: "Middle Linebacker",
  WILL: "Weakside Linebacker", SAM: "Strongside Linebacker", CB: "Cornerback", FS: "Free Safety",
  SS: "Strong Safety", K: "Kicker", P: "Punter", LS: "Long Snapper",
};

// Player-page columns for units without offensive box-score lines (from the Season Stats capture, which lists
// only the leaders in each category; "—" = not listed, not zero).
export type StatCol = { label: string; value: (row: any) => any };
const dash = (v: any) => (v === null || v === undefined ? "—" : v);
const ratio = (made: any, att: any) => (att === null || att === undefined ? "—" : `${made ?? 0}/${att}`);
export const unitGameColumns: Record<string, StatCol[]> = {
  defense: [
    { label: "TKL", value: (g) => dash(g.def_tackles) }, { label: "SCK", value: (g) => dash(g.def_sacks) },
    { label: "TFL", value: (g) => dash(g.def_tfl) }, { label: "INT", value: (g) => dash(g.def_ints) },
    { label: "PD", value: (g) => dash(g.def_deflections) }, { label: "FF", value: (g) => dash(g.def_forced_fumbles) },
    { label: "TD", value: (g) => dash(g.def_tds) },
  ],
  kicking: [
    { label: "FG", value: (g) => ratio(g.kick_fgm, g.kick_fga) }, { label: "LNG", value: (g) => dash(g.kick_fg_long) },
    { label: "XP", value: (g) => ratio(g.kick_xpm, g.kick_xpa) },
  ],
  punting: [
    { label: "PUNTS", value: (g) => dash(g.punt_punts) }, { label: "AVG", value: (g) => dash(g.punt_average) },
    { label: "NET", value: (g) => dash(g.punt_net_average) }, { label: "IN 20", value: (g) => dash(g.punt_inside_20) },
  ],
  blocking: [{ label: "SACKS ALLOWED", value: (g) => dash(g.blk_sacks_allowed) }],
};
export const unitCareerColumns: Record<string, StatCol[]> = {
  defense: [
    { label: "TKL", value: (s) => dash(s.def_tackles) }, { label: "SOLO", value: (s) => dash(s.def_solo) },
    { label: "SCK", value: (s) => dash(s.def_sacks) }, { label: "TFL", value: (s) => dash(s.def_tfl) },
    { label: "INT", value: (s) => dash(s.def_ints) }, { label: "PD", value: (s) => dash(s.def_deflections) },
    { label: "FF", value: (s) => dash(s.def_forced_fumbles) }, { label: "FR", value: (s) => dash(s.def_fumble_recoveries) },
    { label: "TD", value: (s) => dash(s.def_tds) },
  ],
  kicking: [
    { label: "FG", value: (s) => ratio(s.kick_fgm, s.kick_fga) },
    { label: "FG%", value: (s) => (s.kick_fga ? (100 * s.kick_fgm / s.kick_fga).toFixed(1) : "—") },
    { label: "LNG", value: (s) => dash(s.kick_fg_long) }, { label: "50+", value: (s) => ratio(s.kick_fgm_50, s.kick_fga_50) },
    { label: "XP", value: (s) => ratio(s.kick_xpm, s.kick_xpa) },
  ],
  punting: [
    { label: "PUNTS", value: (s) => dash(s.punt_punts) }, { label: "YDS", value: (s) => dash(s.punt_yards) },
    { label: "AVG", value: (s) => dash(s.punt_average) }, { label: "NET", value: (s) => dash(s.punt_net_average) },
    { label: "IN 20", value: (s) => dash(s.punt_inside_20) }, { label: "LNG", value: (s) => dash(s.punt_longest) },
  ],
  blocking: [{ label: "SACKS ALLOWED", value: (s) => dash(s.blk_sacks_allowed) }],
};

export function PlayerPage({ playerId }: { category?: string; playerId: number }) {
  const systemStatus = useContext(SystemContext);
  const [showAllGames, setShowAllGames] = useState(false);
  const [gameLogSeason, setGameLogSeason] = useState(systemStatus.season);
  const [gameLogSeasonOpen, setGameLogSeasonOpen] = useState(false);
  const [playerData, setPlayerData] = useState<any>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/player/${playerId}?season=${gameLogSeason}`)
      .then(res => res.json())
      .then(data => setPlayerData(data))
      .catch(err => console.error("Error loading player:", err));
  }, [playerId, gameLogSeason]);

  // A player who only played in the 2026-2034 league history opens on his last season, not the current one.
  const historyOnlySeasons: any[] = playerData?.career_stats || [];
  const historyOnly = historyOnlySeasons.length > 0 && historyOnlySeasons.every((s: any) => s.history);
  useEffect(() => {
    if (historyOnly && gameLogSeason === systemStatus.season) setGameLogSeason(Number(historyOnlySeasons[0].season));
  }, [historyOnly, gameLogSeason, systemStatus.season]);

  if (!playerData || playerData.error) {
    return <main className="player-page" style={{ padding: "4rem", textAlign: "center" }}>Loading player...</main>;
  }

  const { player, game_log, career_stats } = playerData;
  const careerTotals: any = playerData.career_totals;
  const hasHistory = (career_stats || []).some((s: any) => s.history);
  const selectedIsHistory = (career_stats || []).some((s: any) => s.season === gameLogSeason && s.history);
  
  const visibleGames = showAllGames ? game_log : game_log.slice(-5);
  const isQuarterback = player.position === "QB";
  const positionLabel = POSITION_LABELS[player.position] ?? player.position;
  // Defenders, kickers, punters and linemen get their own columns (null = offensive skill player)
  const unitGroup: string | null = unitGameColumns[playerData.stat_group] ? playerData.stat_group : null;
  const gameCols = unitGroup ? unitGameColumns[unitGroup] : [];
  const careerCols = unitGroup ? unitCareerColumns[unitGroup] : [];
  const missingGameLines = unitGroup !== null && visibleGames.some((g: any) => g.status === "COMPLETED" && !g.has_capture_line);
  
  // Calculate season highlights from career_stats for the selected season
  const currentSeasonStats = (career_stats || []).find((s: any) => s.season === gameLogSeason) || (career_stats || [])[0] || {};
  const listed = currentSeasonStats.has_capture_line;
  const seasonHighlight = unitGroup === "defense"
    ? ["CB", "FS", "SS"].includes(player.position)
      ? { yards: listed ? currentSeasonStats.def_ints ?? 0 : "—", yardsLabel: "INTERCEPTIONS", touchdowns: listed ? currentSeasonStats.def_tackles ?? 0 : "—", touchdownLabel: "TACKLES" }
      : ["LEDG", "REDG", "DT"].includes(player.position)
        ? { yards: listed ? currentSeasonStats.def_sacks ?? 0 : "—", yardsLabel: "SACKS", touchdowns: listed ? currentSeasonStats.def_tfl ?? 0 : "—", touchdownLabel: "TACKLES FOR LOSS" }
        : { yards: listed ? currentSeasonStats.def_tackles ?? 0 : "—", yardsLabel: "TACKLES", touchdowns: listed ? currentSeasonStats.def_sacks ?? 0 : "—", touchdownLabel: "SACKS" }
    : unitGroup === "kicking"
      ? { yards: listed ? `${currentSeasonStats.kick_fgm ?? 0}/${currentSeasonStats.kick_fga ?? 0}` : "—", yardsLabel: "FIELD GOALS", touchdowns: listed ? currentSeasonStats.kick_fg_long ?? 0 : "—", touchdownLabel: "LONG" }
      : unitGroup === "punting"
        ? { yards: listed ? currentSeasonStats.punt_net_average ?? "—" : "—", yardsLabel: "NET PUNT AVERAGE", touchdowns: listed ? currentSeasonStats.punt_inside_20 ?? 0 : "—", touchdownLabel: "INSIDE THE 20" }
        : unitGroup === "blocking"
          ? { yards: currentSeasonStats.gp || 0, yardsLabel: "GAMES PLAYED", touchdowns: listed ? currentSeasonStats.blk_sacks_allowed ?? 0 : "—", touchdownLabel: "SACKS ALLOWED" }
          : isQuarterback
    ? { yards: currentSeasonStats.pass_yards || 0, yardsLabel: "PASSING YARDS", touchdowns: currentSeasonStats.pass_tds || 0, touchdownLabel: "PASSING TD" }
    : ["HB", "FB"].includes(player.position)
      ? { yards: currentSeasonStats.rush_yards || 0, yardsLabel: "RUSHING YARDS", touchdowns: currentSeasonStats.rush_tds || 0, touchdownLabel: "RUSHING TD" }
      : { yards: currentSeasonStats.rec_yards || 0, yardsLabel: "RECEIVING YARDS", touchdowns: currentSeasonStats.rec_tds || 0, touchdownLabel: "RECEIVING TD" };

  const availablePlayerSeasons: number[] = Array.from(
    new Set([
      ...(career_stats || []).map((s: any) => Number(s.season)),
      ...(historyOnly ? [] : [systemStatus.season]),
    ])
  ).filter((s) => !Number.isNaN(s) && s > 2000).sort((a, b) => b - a);

  // Newsroom articles tagged with this player (GET /api/player/{id} `articles`, newest first)
  const playerStories: any[] = Array.isArray(playerData.articles) ? playerData.articles : [];

  return (
    <main className="player-page" style={{ "--team-color": player.team_color || "#e31837" } as React.CSSProperties}>
      <section className="player-profile-hero">
        <div className="player-profile-inner">
          <div className="player-portrait" aria-label="Player portrait placeholder">
            <span>{player.player_number}</span>
            {getPlayerHeadshot(player) !== DEFAULT_AVATAR ? (
              <img
                src={getPlayerHeadshot(player)}
                alt={player.name}
                style={{ width: "100%", maxHeight: "290px", objectFit: "contain", objectPosition: "center bottom", position: "relative", zIndex: 1, filter: "drop-shadow(0 12px 15px #0615254d)" }}
                onError={(e) => {
                  const next = handleHeadshotError(e, player);
                  if (next && next !== DEFAULT_AVATAR) return;
                  e.currentTarget.style.display = "none";
                  const fallback = e.currentTarget.parentElement?.querySelector(".player-portrait-svg-fallback");
                  if (fallback) (fallback as HTMLElement).style.display = "block";
                }}
              />
            ) : null}
            <svg className="player-portrait-svg-fallback" viewBox="0 0 220 250" aria-hidden="true" style={{ display: getPlayerHeadshot(player) !== DEFAULT_AVATAR ? "none" : "block" }}>
              <circle cx="110" cy="69" r="42" />
              <path d="M42 234c4-66 27-105 68-105s64 39 68 105H42Z" />
              <path d="M65 150 32 174l18 60h120l18-60-33-24" />
            </svg>
            <small>PLAYER IMAGE</small>
          </div>
          <div className="player-profile-copy">
            <a href={`#team-${player.team_abbr.toLowerCase()}`}><img src={logo(player.team_abbr.toLowerCase())} alt="" />{player.team_name}</a>
            <span>#{player.player_number} · {positionLabel}</span>
            <h1>{player.name}</h1>
            <div className="player-bio">
              <div><small>POSITION</small><strong>{player.position}</strong></div>
              <div><small>HEIGHT</small><strong>{player.height}</strong></div>
              <div><small>WEIGHT</small><strong>{player.weight} lbs</strong></div>
              <div><small>EXPERIENCE</small><strong>{player.years_of_experience} years</strong></div>
            </div>
            <PlayerHonors awards={playerData.awards} />
            <PlayerCareerPath path={playerData.career_path ?? null} />
          </div>
          <div className="player-season-card">
            <span>{gameLogSeason} SEASON</span>
            <strong>{seasonHighlight.yards}</strong>
            <small>{seasonHighlight.yardsLabel}</small>
            <div><b>{seasonHighlight.touchdowns}</b><span>{seasonHighlight.touchdownLabel}</span></div>
          </div>
        </div>
      </section>

      <div className="player-data-layout">
        <section className="player-log-section">
          <div className="player-log-heading">
            <div className="team-section-title"><span>{gameLogSeason} Regular Season</span><h2>Game Log</h2></div>
            <div className="player-log-actions">
              <div className="game-log-season-select" onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setGameLogSeasonOpen(false);
              }}>
                <span>Season</span>
                <button className={gameLogSeasonOpen ? "open" : ""} onClick={() => setGameLogSeasonOpen(!gameLogSeasonOpen)} aria-expanded={gameLogSeasonOpen}>
                  <strong>{gameLogSeason}</strong><Chevron />
                </button>
                {gameLogSeasonOpen && (
                  <div className="game-log-season-menu">
                    {availablePlayerSeasons.map((year) => (
                      <button
                        className={gameLogSeason === year ? "selected" : ""}
                        onClick={() => { setGameLogSeason(year); setGameLogSeasonOpen(false); }}
                        key={year}
                      >
                        <span>{year}</span>
                        <small>{year === systemStatus.season ? "Current" : "Season"}</small>
                        {gameLogSeason === year && <i>✓</i>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {!selectedIsHistory && (
              <button onClick={() => setShowAllGames(!showAllGames)}>
                {showAllGames ? "Show last 5 games" : "Show all 17 games"}
                <Chevron direction={showAllGames ? "up" : "down"} />
              </button>
              )}
            </div>
          </div>
          {selectedIsHistory ? (
            <p className="player-log-note">
              {gameLogSeason} is reconstructed league history: the season totals are in Career Stats below, and no
              game logs were kept.
            </p>
          ) : (
          <div className="player-table-scroll">
            <table className="player-history-table">
              <thead>
                <tr>
                  <th>WK</th><th>OPPONENT</th><th>RESULT</th>
                  {unitGroup
                    ? gameCols.map((col) => <th key={col.label}>{col.label}</th>)
                    : isQuarterback
                    ? <><th>CMP/ATT</th><th>PASS YDS</th><th>PASS TD</th><th>INT</th><th>CAR</th><th>RUSH YDS</th><th>RUSH TD</th></>
                    : <><th>CAR</th><th>RUSH YDS</th><th>RUSH TD</th><th>REC</th><th>REC YDS</th><th>REC TD</th></>}
                </tr>
              </thead>
              <tbody>
                {visibleGames.map((game: any) => {
                  const isHome = game.home_team_id === player.team_id;
                  const opponent_name = isHome ? game.away_name : game.home_name;
                  const opponent_abbr = isHome ? game.away_abbr.toLowerCase() : game.home_abbr.toLowerCase();
                  const location = isHome ? "vs" : "at";
                  const teamScore = isHome ? game.home_score : game.away_score;
                  const oppScore = isHome ? game.away_score : game.home_score;
                  const isWin = teamScore > oppScore;
                  
                  return (
                    <tr key={game.week}>
                      <td>{game.week}</td>
                      <td><a href={`#team-${opponent_abbr}`}><img src={logo(opponent_abbr)} alt="" /><span>{location} {opponent_name}</span></a></td>
                      <td>{game.status === 'COMPLETED' ? <><b className={isWin ? "win" : "loss"}>{isWin ? "W" : "L"}</b> {Math.max(teamScore, oppScore)}–{Math.min(teamScore, oppScore)}</> : "-"}</td>
                      {unitGroup ? (
                        gameCols.map((col) => <td key={col.label}>{game.status === "COMPLETED" ? col.value(game) : "—"}</td>)
                      ) : isQuarterback ? (
                        <>
                          <td>{game.completions || 0}/{game.pass_attempts || 0}</td>
                          <td>{game.pass_yards || 0}</td>
                          <td>{game.pass_tds || 0}</td>
                          <td>{game.interceptions || 0}</td>
                          <td>{game.rush_attempts || 0}</td>
                          <td>{game.rush_yards || 0}</td>
                          <td>{game.rush_tds || 0}</td>
                        </>
                      ) : (
                        <>
                          <td>{game.rush_attempts || 0}</td>
                          <td>{game.rush_yards || 0}</td>
                          <td>{game.rush_tds || 0}</td>
                          <td>{game.receptions || 0}</td>
                          <td>{game.rec_yards || 0}</td>
                          <td>{game.rec_tds || 0}</td>
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          )}
          {missingGameLines && !selectedIsHistory && (
            <p className="player-log-note">— means no line was recorded for that game. Season totals are in Career Stats.</p>
          )}
        </section>

        <section className="player-career-section">
          <div className="team-section-title"><span>Career Production</span><h2>Career Stats</h2></div>
          <div className="player-table-scroll">
            <table className="player-history-table career-table">
              <thead>
                <tr>
                  <th>SEASON</th><th>TEAM</th><th>GP</th>
                  {unitGroup
                    ? careerCols.map((col) => <th key={col.label}>{col.label}</th>)
                    : isQuarterback
                    ? <><th>CMP/ATT</th><th>PASS YDS</th><th>PASS TD</th><th>RUSH YDS</th><th>RUSH TD</th></>
                    : <><th>CAR</th><th>RUSH YDS</th><th>RUSH TD</th><th>REC</th><th>REC YDS</th><th>REC TD</th></>}
                </tr>
              </thead>
              <tbody>
                {career_stats.map((s: any) => {
                  const isSelected = gameLogSeason === s.season;
                  return (
                    <tr
                      key={s.season}
                      onClick={() => setGameLogSeason(s.season)}
                      style={{ cursor: "pointer", background: isSelected ? "rgba(227, 24, 55, 0.08)" : undefined }}
                      className={isSelected ? "selected-season-row" : ""}
                      title={`Click to view ${s.season} season stats and game log`}
                    >
                      <td>
                        <strong>{s.season}</strong>
                        {s.history && <span className="career-history-tag" title="Reconstructed league history (2026–2034)">hist</span>}
                        {isSelected && <span style={{ marginLeft: "6px", color: "var(--team-color, #e31837)", fontSize: "11px" }}>●</span>}
                      </td>
                      <td><a href={`#team-${s.team_abbr.toLowerCase()}`} onClick={(e) => e.stopPropagation()}><img src={logo(s.team_abbr.toLowerCase())} alt="" /><span>{s.team_name}</span></a></td>
                      <td>{s.gp || 0}</td>
                    {unitGroup ? (
                      careerCols.map((col) => <td key={col.label}>{col.value(s)}</td>)
                    ) : isQuarterback ? (
                      <>
                        <td>{s.completions || 0}/{s.pass_attempts || 0}</td>
                        <td>{s.pass_yards || 0}</td>
                        <td>{s.pass_tds || 0}</td>
                        <td>{s.rush_yards || 0}</td>
                        <td>{s.rush_tds || 0}</td>
                      </>
                    ) : (
                      <>
                        <td>{s.rush_attempts || 0}</td>
                        <td>{s.rush_yards || 0}</td>
                        <td>{s.rush_tds || 0}</td>
                        <td>{s.receptions || 0}</td>
                        <td>{s.rec_yards || 0}</td>
                        <td>{s.rec_tds || 0}</td>
                      </>
                    )}
                  </tr>
                );
              })}
              {careerTotals && career_stats.length > 1 && (
                <tr className="career-total-row">
                  <td><strong>Career</strong><span className="career-since">since {careerTotals.since}</span></td>
                  <td>{careerTotals.seasons} seasons</td>
                  <td>{careerTotals.gp || 0}</td>
                  {unitGroup ? (
                    careerCols.map((col) => <td key={col.label}>{col.value(careerTotals)}</td>)
                  ) : isQuarterback ? (
                    <>
                      <td>{careerTotals.completions || 0}/{careerTotals.pass_attempts || 0}</td>
                      <td>{careerTotals.pass_yards || 0}</td>
                      <td>{careerTotals.pass_tds || 0}</td>
                      <td>{careerTotals.rush_yards || 0}</td>
                      <td>{careerTotals.rush_tds || 0}</td>
                    </>
                  ) : (
                    <>
                      <td>{careerTotals.rush_attempts || 0}</td>
                      <td>{careerTotals.rush_yards || 0}</td>
                      <td>{careerTotals.rush_tds || 0}</td>
                      <td>{careerTotals.receptions || 0}</td>
                      <td>{careerTotals.rec_yards || 0}</td>
                      <td>{careerTotals.rec_tds || 0}</td>
                    </>
                  )}
                </tr>
              )}
              </tbody>
            </table>
          </div>
          {hasHistory && (
            <p className="player-log-note">
              <span className="career-history-tag">hist</span> seasons are reconstructed league history (2026–2034): season
              totals only, no game logs. Career totals count every season in this league, since 2026.
            </p>
          )}
        </section>

        {playerStories.length > 0 && (
        <section className="player-stories-section">
          <div className="team-section-title"><h2>Player Coverage</h2></div>
          <div className="player-story-list">
            {playerStories.map((story: any, index: number) => (
              <article className="player-story-card" key={story.id}>
                <div className="player-story-index">{String(index + 1).padStart(2, "0")}</div>
                <div className="player-story-team"><img src={logo(player.team_abbr.toLowerCase())} alt="" /><span>#{player.player_number}</span></div>
                <div className="player-story-copy">
                  <span>{story.label}</span>
                  <a href={`#article-${story.id}`}><h3>{story.headline}</h3></a>
                  {story.excerpt && <p>{story.excerpt}</p>}
                  <small>By {story.author} · {story.season} Week {story.week}</small>
                </div>
                <a className="player-story-arrow" href={`#article-${story.id}`} aria-label={`Read ${story.headline}`}>→</a>
              </article>
            ))}
          </div>
        </section>
        )}
      </div>
    </main>
  );
}
