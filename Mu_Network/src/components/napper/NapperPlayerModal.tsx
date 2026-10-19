import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperDraftPlayer, NapperStarterPlayer, NapperWaiverPlayer } from "../../types/napper";
import { getPlayerHeadshotUrl, handleImageError, InjuryPill, injuryDescription } from "./napperUtils";

type NapperPlayer = NapperStarterPlayer | NapperWaiverPlayer | NapperDraftPlayer;
type DetailTab = "logs" | "stats" | "outlook";

interface NapperPlayerModalProps {
  player: NapperPlayer;
  onClose: () => void;
  scoringType?: string;
}

const teamLogo = (abbr?: string) => abbr
  ? `https://a.espncdn.com/i/teamlogos/nfl/500/${abbr.toLowerCase()}.png`
  : "";

function score(game: any): string {
  if (game.status !== "COMPLETED") return "—";
  if (game.fantasy_points != null) return Number(game.fantasy_points).toFixed(1);
  const points = (Number(game.pass_yards || 0) * 0.04)
    + (Number(game.pass_tds || 0) * 4)
    - (Number(game.interceptions || 0) * 2)
    + (Number(game.rush_yards || 0) * 0.1)
    + (Number(game.rush_tds || 0) * 6)
    + (Number(game.rec_yards || 0) * 0.1)
    + (Number(game.rec_tds || 0) * 6)
    + Number(game.receptions || 0)
    - (Number(game.fumb || 0) * 2);
  return points.toFixed(1);
}

function value(number: unknown, digits = 1): string {
  if (number == null || number === "") return "—";
  const parsed = Number(number);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : String(number);
}

export default function NapperPlayerModal({ player: source, onClose, scoringType = "PPR" }: NapperPlayerModalProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>("logs");
  const [showAllSeasons, setShowAllSeasons] = useState(false);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    fetch(`${API_BASE}/api/player/${source.player_id}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Player details could not be loaded.");
        return response.json();
      })
      .then((result) => {
        if (result?.error) throw new Error(result.error);
        setData(result);
      })
      .catch((fetchError) => {
        if (fetchError?.name !== "AbortError") setError(true);
      });
    return () => controller.abort();
  }, [source.player_id]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const player = data?.player || {};
  const playerId = source.player_id;
  const name = player.name || source.name;
  const position = player.position || source.position;
  const teamAbbr = player.team_abbr || player.team || source.team;
  const currentWeek = Number(data?.weekly_outlook?.week || 1);
  const currentSeason = player.season || data?.career_stats?.[0]?.season || "Current";
  const outlook = data?.weekly_outlook || {};
  const gameLog = Array.isArray(data?.game_log) ? data.game_log : [];
  const careerStats = Array.isArray(data?.career_stats) ? data.career_stats : [];
  const completedGames = gameLog.filter((game: any) => game.status === "COMPLETED");
  const sourceProjection = "proj_fpts" in source ? source.proj_fpts : "proj_avg" in source ? source.proj_avg : null;
  const sourceOpponent = "opponent" in source ? source.opponent : undefined;
  const projection = outlook.projection ?? sourceProjection ?? "—";
  const isQuarterback = position === "QB";
  const isRushPrimary = ["HB", "RB", "FB"].includes(position);
  const positionRank = outlook.pos_rank || outlook.ecr_rank || "—";
  const restOfSeasonRank = outlook.ros_rank || "—";
  const home = outlook.is_home !== undefined
    ? outlook.is_home
    : gameLog.find((game: any) => game.week === currentWeek)?.home_team_id === player.team_id;
  const currentGame = gameLog.find((game: any) => game.week === currentWeek);
  const opponent = outlook.opp_abbr || (currentGame
    ? (currentGame.home_team_id === player.team_id ? currentGame.away_abbr : currentGame.home_abbr)
    : sourceOpponent || "—");
  const matchupText = outlook.bye ? "BYE" : `${home ? "vs" : "@"} ${opponent}`;

  const jumpTo = (section: DetailTab) => {
    setActiveTab(section);
    document.getElementById(`napper-player-${playerId}-${section}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const columns = isQuarterback
    ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
    : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)";

  if (!data && !error) {
    return (
      <div className="napper-player-modal">
        <button className="fantasy-modal-scrim" onClick={onClose} aria-label="Close modal" />
        <div className="fantasy-player-dialog napper-player-dialog napper-player-loading" role="dialog" aria-modal="true" aria-label={`Loading ${name} analytics`}>
          <button className="fantasy-modal-close" onClick={onClose} aria-label="Close player details">×</button>
          <p>Loading player analytics…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="napper-player-modal">
      <button className="fantasy-modal-scrim" onClick={onClose} aria-label="Close modal" />
      <div className="fantasy-player-dialog napper-player-dialog" role="dialog" aria-modal="true" aria-labelledby="napper-player-title">
        <button className="fantasy-modal-close" onClick={onClose} aria-label="Close player details">×</button>

        <header className="fantasy-player-hero napper-player-hero">
          <div className="fantasy-modal-portrait">
            <span />
            <img
              src={getPlayerHeadshotUrl(playerId, player.headshot_url || source.headshot_url, player.espn_id || source.espn_id)}
              alt={name}
              onError={handleImageError}
            />
          </div>

          <div className="fantasy-modal-identity">
            <h2 id="napper-player-title">{name}</h2>
            {"injury_status" in player && player.injury_status && (
              <div className="napper-modal-injury">
                <InjuryPill player={player} /> <span>{injuryDescription(player)}{player.injury_duration ? ` (${player.injury_duration.toLowerCase()})` : ""}</span>
              </div>
            )}
            <div className="fantasy-modal-bio">
              <strong>{position} — {teamAbbr}</strong>
              <i />
              <b>Week {currentWeek}</b>
              <i />
              <span>{"rostered_pct" in source && source.rostered_pct != null ? `${source.rostered_pct}% Rostered` : "Roster data unavailable"}</span>
            </div>

            <div className="fantasy-modal-metrics">
              <div><small>WK {currentWeek} RANK</small><strong>{positionRank}</strong></div>
              <div><small>{scoringType.toUpperCase()} PROJ</small><strong className="napper-projection-value">{value(projection)}</strong></div>
              <div><small>MATCHUP</small><strong>{matchupText}</strong></div>
              <div><small>ROS</small><strong>{restOfSeasonRank}</strong></div>
            </div>
          </div>

          <div className="fantasy-modal-team napper-modal-team">
            {teamLogo(teamAbbr) && <img src={teamLogo(teamAbbr)} alt={player.team_name || teamAbbr} />}
            <span>{scoringType.toUpperCase()}</span>
          </div>
        </header>

        <nav className="fantasy-modal-tabs napper-modal-tabs" aria-label="Player analytics sections">
          {(["logs", "stats", "outlook"] as DetailTab[]).map((tab) => (
            <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => jumpTo(tab)}>
              {tab === "logs" ? "Game Logs" : tab === "stats" ? "Season Stats" : "Outlook"}
            </button>
          ))}
          <a href={`#player-${playerId}`} onClick={onClose}>Full profile ↗</a>
        </nav>

        <div className="fantasy-modal-content napper-modal-content">
          {error && (
            <div className="napper-player-empty">Player analytics are unavailable right now.</div>
          )}

          <section className="fantasy-modal-log" id={`napper-player-${playerId}-logs`}>
            <div className="fantasy-modal-section-head">
              <div><span>{currentSeason} SEASON</span><h3>GAME LOGS</h3></div>
              <b>{scoringType.toUpperCase()} SCORING</b>
            </div>

            <div className="fantasy-season-table-scroll">
              <div className="fantasy-season-groups" style={{ gridTemplateColumns: columns }}>
                <span style={{ gridColumn: "1 / 3" }} />
                <strong style={{ gridColumn: "3 / 5" }}>FANTASY</strong>
                <strong style={{ gridColumn: isQuarterback ? "5 / 11" : "5 / 9" }}>{isQuarterback ? "PASSING" : "RUSHING"}</strong>
                <strong style={{ gridColumn: isQuarterback ? "11 / 15" : "9 / 13" }}>{isQuarterback ? "RUSHING" : "RECEIVING"}</strong>
              </div>
              <div className="fantasy-season-labels" style={{ gridTemplateColumns: columns }}>
                <span>WK</span><span>OPP</span><span>FPTS</span><span>FIN</span>
                {isQuarterback ? <><span>CMP</span><span>ATT</span><span>Y/A</span><span>YARDS</span><span>TDS</span><span>INTS</span><span>ATT</span><span>YARDS</span><span>Y/A</span><span>TDS</span></> : <><span>ATT</span><span>YARDS</span><span>Y/A</span><span>TDS</span><span>REC</span><span>YARDS</span><span>Y/R</span><span>TDS</span></>}
              </div>
              <div className="fantasy-season-values-wrap">
                {gameLog.map((game: any, index: number) => {
                  const isBye = game.status === "BYE";
                  const isHome = game.home_team_id === player.team_id;
                  const opponentAbbr = isBye ? "BYE" : (isHome ? game.away_abbr : game.home_abbr);
                  const completed = game.status === "COMPLETED";
                  const passAtt = Number(game.pass_attempts || 0);
                  const rushAtt = Number(game.rush_attempts || 0);
                  const receptions = Number(game.receptions || 0);
                  const rushYards = Number(game.rush_yards || 0);
                  const receivingYards = Number(game.rec_yards || 0);
                  return (
                    <div className="fantasy-season-values" key={game.week || index} style={{ gridTemplateColumns: columns, opacity: isBye ? 0.6 : 1 }}>
                      <strong>{game.week}</strong>
                      <strong>{isBye ? "BYE" : `${isHome ? "vs" : "@"} ${opponentAbbr}`}</strong>
                      <strong className={completed ? "napper-fpts-highlight" : ""}>{isBye ? "—" : score(game)}</strong>
                      <strong>{completed ? game.weekly_pos_rank || `${position}—` : "—"}</strong>
                      {isQuarterback ? <>
                        <strong>{completed ? game.completions ?? 0 : "—"}</strong><strong>{completed ? passAtt : "—"}</strong><strong>{completed && passAtt ? (Number(game.pass_yards || 0) / passAtt).toFixed(1) : completed ? "0.0" : "—"}</strong><strong className={completed ? "stat-highlight" : ""}>{completed ? game.pass_yards ?? 0 : "—"}</strong><strong>{completed ? game.pass_tds ?? 0 : "—"}</strong><strong>{completed ? game.interceptions ?? 0 : "—"}</strong><strong>{completed ? rushAtt : "—"}</strong><strong>{completed ? rushYards : "—"}</strong><strong>{completed && rushAtt ? (rushYards / rushAtt).toFixed(1) : completed ? "0.0" : "—"}</strong><strong>{completed ? game.rush_tds ?? 0 : "—"}</strong>
                      </> : <>
                        <strong>{completed ? rushAtt : "—"}</strong><strong className={completed && isRushPrimary ? "stat-highlight" : ""}>{completed ? rushYards : "—"}</strong><strong>{completed && rushAtt ? (rushYards / rushAtt).toFixed(1) : completed ? "0.0" : "—"}</strong><strong>{completed ? game.rush_tds ?? 0 : "—"}</strong><strong>{completed ? receptions : "—"}</strong><strong className={completed && !isRushPrimary ? "stat-highlight" : ""}>{completed ? receivingYards : "—"}</strong><strong>{completed && receptions ? (receivingYards / receptions).toFixed(1) : completed ? "0.0" : "—"}</strong><strong>{completed ? game.rec_tds ?? 0 : "—"}</strong>
                      </>}
                    </div>
                  );
                })}
              </div>
              {!gameLog.length && <div className="napper-player-empty">No game log is available for this season.</div>}
            </div>
          </section>

          <section className="fantasy-modal-season-section" id={`napper-player-${playerId}-stats`}>
            <div className="fantasy-modal-section-head">
              <div><span>{currentSeason} SEASON</span><h3>SEASON STATS</h3></div>
              <b>THROUGH WEEK {Math.max(1, currentWeek - 1)}</b>
            </div>
            <div className="fantasy-season-table-scroll">
              <div className="fantasy-season-groups" style={{ gridTemplateColumns: columns }}>
                <span style={{ gridColumn: "1 / 3" }} /><strong style={{ gridColumn: "3 / 5" }}>FANTASY</strong>
                <strong style={{ gridColumn: isQuarterback ? "5 / 11" : "5 / 9" }}>{isQuarterback ? "PASSING" : "RUSHING"}</strong>
                <strong style={{ gridColumn: isQuarterback ? "11 / 15" : "9 / 13" }}>{isQuarterback ? "RUSHING" : "RECEIVING"}</strong>
              </div>
              <div className="fantasy-season-labels" style={{ gridTemplateColumns: columns }}>
                <span>YR</span><span>TM</span><span>FPTS</span><span>FIN</span>
                {isQuarterback ? <><span>CMP</span><span>ATT</span><span>Y/A</span><span>YARDS</span><span>TDS</span><span>INTS</span><span>ATT</span><span>YARDS</span><span>Y/A</span><span>TDS</span></> : <><span>ATT</span><span>YARDS</span><span>Y/A</span><span>TDS</span><span>REC</span><span>YARDS</span><span>Y/R</span><span>TDS</span></>}
              </div>
              <div className={`fantasy-season-values-wrap ${showAllSeasons ? "expanded" : ""}`}>
                {careerStats.map((stats: any, index: number) => {
                  const passAtt = Number(stats.pass_attempts || 0);
                  const rushAtt = Number(stats.rush_attempts || 0);
                  const receptions = Number(stats.receptions || 0);
                  const rushYards = Number(stats.rush_yards || 0);
                  const receivingYards = Number(stats.rec_yards || 0);
                  return <div className="fantasy-season-values" key={stats.season || index} style={{ gridTemplateColumns: columns }}>
                    <strong>'{String(stats.season || currentSeason).slice(-2)}</strong><strong>{stats.team_abbr || teamAbbr}</strong>
                    <strong className="napper-season-points">{stats.total_fpts == null ? "—" : Number(stats.total_fpts).toFixed(1)}</strong><strong>#{stats.overall_rank || "—"}</strong>
                    {isQuarterback ? <>
                      <strong>{stats.completions ?? "—"}</strong><strong>{stats.pass_attempts ?? "—"}</strong><strong>{passAtt ? (Number(stats.pass_yards || 0) / passAtt).toFixed(1) : "—"}</strong><strong className="stat-highlight">{stats.pass_yards ?? "—"}</strong><strong>{stats.pass_tds ?? "—"}</strong><strong>{stats.interceptions ?? "—"}</strong><strong>{stats.rush_attempts ?? "—"}</strong><strong>{stats.rush_yards ?? "—"}</strong><strong>{rushAtt ? (rushYards / rushAtt).toFixed(1) : "—"}</strong><strong>{stats.rush_tds ?? "—"}</strong>
                    </> : <>
                      <strong>{stats.rush_attempts ?? "—"}</strong><strong className={isRushPrimary ? "stat-highlight" : ""}>{stats.rush_yards ?? "—"}</strong><strong>{rushAtt ? (rushYards / rushAtt).toFixed(1) : "—"}</strong><strong>{stats.rush_tds ?? "—"}</strong><strong>{stats.receptions ?? "—"}</strong><strong className={!isRushPrimary ? "stat-highlight" : ""}>{stats.rec_yards ?? "—"}</strong><strong>{receptions ? (receivingYards / receptions).toFixed(1) : "—"}</strong><strong>{stats.rec_tds ?? "—"}</strong>
                    </>}
                  </div>;
                })}
              </div>
              {!careerStats.length && <div className="napper-player-empty">Season statistics are not available.</div>}
              <button className="fantasy-more-seasons" onClick={() => setShowAllSeasons(!showAllSeasons)}>{showAllSeasons ? "Show fewer seasons ↑" : "View more seasons ↓"}</button>
            </div>
          </section>

          <section className="fantasy-matchup-outlook" id={`napper-player-${playerId}-outlook`}>
            <div className="fantasy-modal-section-head"><div><span>LINEUP OUTLOOK</span><h3>WEEK {currentWeek} OUTLOOK</h3></div></div>
            {outlook.bye ? (
              <div className="fantasy-matchup-card" style={{ marginTop: 16 }}>
                <div className="fantasy-kickoff"><span /><strong>BYE WEEK</strong><span /></div>
                <p className="napper-bye-copy">{teamAbbr} doesn’t play in Week {outlook.week}.</p>
                <div className="fantasy-outlook-metrics"><div><strong>—</strong><span>ECR</span></div><div><strong>0.0</strong><span>Projection</span></div><div><strong>{outlook.ros_rank || "—"}</strong><span>ROS</span></div></div>
              </div>
            ) : outlook.week != null ? (
              <div className="fantasy-matchup-card" style={{ marginTop: 16 }}>
                <div className="fantasy-matchup-banner napper-outlook-banner">
                  <div><img src={teamLogo(outlook.home_abbr)} alt={outlook.home_name || "Home"} /><span><small>{outlook.home_name?.toUpperCase() || "HOME"}</small><strong>{outlook.home_abbr || "—"}</strong></span></div>
                  <i>VS</i>
                  <div><span><small>{outlook.away_name?.toUpperCase() || "AWAY"}</small><strong>{outlook.away_abbr || "—"}</strong></span><img src={teamLogo(outlook.away_abbr)} alt={outlook.away_name || "Away"} /></div>
                </div>
                <div className="fantasy-kickoff"><span /><strong>{outlook.kickoff || "Kickoff TBD"}</strong><span /></div>
                <div className="fantasy-outlook-metrics">
                  <div><strong>{outlook.ecr_rank || "—"}</strong><span>ECR</span></div>
                  <div><strong>{value(projection)}</strong><span>Projection</span></div>
                  <div>{outlook.stars ? <div className="fantasy-stars">{"★".repeat(Math.max(1, Math.min(5, Number(outlook.stars)) ))}<i>{"★".repeat(5 - Math.max(1, Math.min(5, Number(outlook.stars))))}</i></div> : <strong>—</strong>}<span>Matchup</span></div>
                </div>
                {outlook.projected === false && <p className="napper-estimate-note">Estimated projection; no stored weekly projection is available.</p>}
              </div>
            ) : <div className="napper-player-empty">A weekly outlook is not available.</div>}
          </section>
        </div>
      </div>
    </div>
  );
}
