import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerHeadshot, handleHeadshotError } from "../../lib/gridiron";
import { FantasyHeader } from "./FantasyHeader";
import { fantasyRankingPlayers, type FantasyRankingsView, useFantasyPlayer } from "./FantasyPlayerContext";

export function FantasyRankingsPage({ view }: { view: FantasyRankingsView }) {
  const systemStatus = useContext(SystemContext);
  const { openPlayer } = useFantasyPlayer();
  const [position, setPosition] = useState("FLEX");
  const [week, setWeek] = useState(systemStatus.week || 1);
  const [visibleCount, setVisibleCount] = useState(50);
  const [dbData, setDbData] = useState<any>(null);
  const [dbLoading, setDbLoading] = useState(false);

  useEffect(() => {
    if (systemStatus.week) setWeek(systemStatus.week);
  }, [systemStatus.week]);

  const isWeekly = view === "weekly";
  const isRos = view === "ros";
  const isAdp = view === "adp";
  const isDbView = isWeekly || isRos || isAdp;

  useEffect(() => {
    if (!isDbView) return;
    setDbLoading(true);
    setVisibleCount(50);
    const endpoint = isWeekly ? "weekly" : isRos ? "ros" : "adp";
    const posParam = isAdp ? "ALL" : position;
    fetch(`${API_BASE}/api/fantasy/rankings/${endpoint}?season=${systemStatus.season}&week=${week}&position=${posParam}&limit=500`)
      .then((res) => res.json())
      .then((data) => setDbData(data))
      .catch(() => setDbData(null))
      .finally(() => setDbLoading(false));
  }, [view, systemStatus.season, week, position, isDbView, isWeekly, isRos, isAdp]);

  const weeklyMode: "projected" | "final" | "unavailable" = dbData?.mode || "unavailable";
  const dbPlayers: any[] = dbData?.players || [];
  const filteredPlayers: any[] = isDbView
    ? dbPlayers.slice(0, visibleCount)
    : fantasyRankingPlayers.filter((player) => position === "FLEX" || player.position === position);
  const totalPlayers = isDbView ? dbPlayers.length : filteredPlayers.length;
  const viewCopy = {
    weekly: {
      eyebrow: weeklyMode === "final" ? "FINAL RESULTS" : "CURRENT WEEK",
      title: `Week ${week} Fantasy Football Rankings`,
      description: weeklyMode === "final"
        ? `Final PPR finishes for Week ${week}, ranked by actual fantasy points scored.`
        : "PPR rankings built from matchup quality, opportunity, and our latest player projections.",
      value: weeklyMode === "final" ? "FPTS" : "PROJ. FPTS",
    },
    adp: { eyebrow: "DRAFT MARKET", title: `${systemStatus.season} Preseason ADP`, description: "Average draft position for PPR redraft leagues with team bye weeks, rest-of-season position ranks, and schedule strength.", value: "AVG. PICK" },
    projections: { eyebrow: "WEEKLY OUTLOOK", title: `Week ${week} Player Projections`, description: `Statistical PPR projections for every fantasy-relevant player on the Week ${week} slate.`, value: "PROJ. FPTS" },
    ros: { eyebrow: "SEASON LONG", title: "Rest of Season Rankings", description: "Forward-looking PPR rankings accounting for role stability, schedule strength, and playoff-week upside.", value: "PROJECTED ROS FPTS" },
  }[view];
  const displayedValue = (player: any, index: number) => {
    if (view === "adp") return `${index + 1}.${(index * 3) % 10}`;
    return player.value;
  };
  const trendTitle = (player: any) =>
    isWeekly && player.prev_finish
      ? `Week ${week - 1} finish: ${player.prev_finish}`
      : isRos && player.adp_rank
      ? `Preseason ADP: #${player.adp_rank} · ROS: #${player.overall_rank || player.rank}`
      : undefined;
  const showPosColumn = isDbView && position === "FLEX";
  const rankingGridStyle = isWeekly
    ? {
        gridTemplateColumns: showPosColumn
          ? "60px minmax(230px, 1fr) 70px 95px 95px 95px 65px"
          : "60px minmax(240px, 1fr) 100px 100px 100px 70px",
        minWidth: showPosColumn ? "780px" : "720px",
      }
    : isRos
    ? {
        gridTemplateColumns: showPosColumn
          ? "60px minmax(220px, 1fr) 80px 115px 115px 145px 70px"
          : "60px minmax(240px, 1fr) 120px 120px 155px 75px",
        minWidth: showPosColumn ? "810px" : "770px",
      }
    : isAdp
    ? {
        gridTemplateColumns: "60px minmax(240px, 1fr) 100px 125px 125px 125px",
        minWidth: "775px",
      }
    : undefined;

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="rankings" />
      <main className="fantasy-rankings-main">
        <div className="fantasy-rankings-status">
          <a href="#fantasy-rankings">Rankings home</a>
          <span>{systemStatus.season} season</span>
          <b>PPR scoring</b>
        </div>
        <div className="fantasy-rankings-layout">
          <aside className="fantasy-rankings-rail">
            <a className="fantasy-rail-home" href="#fantasy-rankings">
              <span>R</span><div><strong>NFL Rankings Home</strong><small>YOUR WEEKLY COMMAND CENTER</small></div>
            </a>
            <section>
              <a className={view === "weekly" ? "active" : ""} href="#fantasy-rankings">Weekly Rankings <b>→</b></a>
              <a className={view === "ros" ? "active" : ""} href="#fantasy-ros">ROS Rankings <b>→</b></a>
              <a className={view === "adp" ? "active" : ""} href="#fantasy-adp">Preseason ADP <b>→</b></a>
            </section>
            <div className="fantasy-rail-note">
              <span>PPR</span>
              <strong>One scoring system.<br />No second guessing.</strong>
              <p>Every ranking and projection across Mu uses full-point PPR scoring.</p>
            </div>
          </aside>

          <section className="fantasy-rankings-content">
            <header className="fantasy-rankings-title">
              <span>{viewCopy.eyebrow}</span>
              <h1>{viewCopy.title}</h1>
              <p>{viewCopy.description}</p>
            </header>

            <div className="fantasy-rankings-board">
              <div className="fantasy-board-intro">
                <div><span>FEATURED BOARD</span><h2>{viewCopy.title}</h2><p>{isWeekly ? (weeklyMode === "final" ? `Week ${week} is final — ranked by actual PPR points scored.` : `Ranked by projected PPR points for Week ${week}. Trend compares to each player's Week ${Math.max(1, week - 1)} positional finish.`) : isRos ? "Forward-looking PPR rest-of-season rankings combining points scored to date with future projections." : isAdp ? "Preseason consensus average draft position with team bye weeks, positional order, and schedule strength." : "Updated today at 9:42 AM ET by the Mu fantasy intelligence team."}</p></div>
                {isWeekly ? (
                  <div className="fantasy-week-control">
                    <button onClick={() => setWeek(Math.max(1, week - 1))} aria-label="Previous week">←</button>
                    <span><small>NFL WEEK</small><strong>{week}</strong></span>
                    <button onClick={() => setWeek(Math.min(18, week + 1))} aria-label="Next week">→</button>
                  </div>
                ) : null}
              </div>

              {!isAdp ? (
                <div className="fantasy-ranking-filters">
                  <div><span>POSITION</span><div>{["QB", "RB", "WR", "TE", "FLEX"].map((item) => <button className={position === item ? "active" : ""} onClick={() => setPosition(item)} key={item}>{item}</button>)}</div></div>
                </div>
              ) : null}

              <div className="fantasy-ranking-table">
                <div className="fantasy-ranking-head" style={rankingGridStyle}>
                  <span>RANK</span>
                  <span>PLAYER</span>
                  {isAdp ? (
                    <>
                      <span>POSITION</span>
                      <span>TEAM (BYE)</span>
                      <span>SEASON SOS</span>
                      <span>PLAYOFF SOS</span>
                    </>
                  ) : (
                    <>
                      {showPosColumn ? <span>{isRos ? "ROS RANK" : "POS"}</span> : null}
                      {isWeekly ? <span>OPPONENT</span> : null}
                      {isWeekly ? <span>MATCHUP</span> : null}
                      {isRos ? <span>SEASON SOS</span> : null}
                      {isRos ? <span>PLAYOFF SOS</span> : null}
                      <span>{viewCopy.value}</span>
                      <span>TREND</span>
                    </>
                  )}
                </div>
                {isDbView && dbLoading && filteredPlayers.length === 0 ? (
                  <div className="fantasy-board-footer"><span>Loading rankings…</span></div>
                ) : isDbView && filteredPlayers.length === 0 ? (
                  <div className="fantasy-board-footer"><span>No rankings available yet.</span></div>
                ) : null}
                {filteredPlayers.map((player, index) => (
                  <button className="fantasy-ranking-row" style={rankingGridStyle} onClick={() => openPlayer(player)} key={player.id || player.name}>
                    <strong>{player.rank || index + 1}</strong>
                    <div className="fantasy-ranked-player">
                      <span>
                        <img
                          src={getPlayerHeadshot(player)}
                          alt=""
                          onError={(e) => handleHeadshotError(e, player)}
                        />
                      </span>
                      <div><strong>{player.name}</strong><small><b>{player.position}</b>{isAdp ? "" : player.team}</small></div>
                    </div>
                    {isAdp ? (
                      <>
                        <span className="fantasy-pos-rank">{player.pos_rank}</span>
                        <span style={{ fontWeight: 600, color: "var(--fantasy-subtext, #8da0b8)" }}>{player.team_bye || `${player.team} (${player.bye_week || "—"})`}</span>
                        <span className="fantasy-stars" title={`${player.season_sos || 3}/5 season SOS`}>
                          {"★".repeat(player.season_sos || 3)}
                          <i>{"★".repeat(5 - (player.season_sos || 3))}</i>
                        </span>
                        <span className="fantasy-stars" title={`${player.playoff_sos || 3}/5 playoff SOS (Wks 15-17)`}>
                          {"★".repeat(player.playoff_sos || 3)}
                          <i>{"★".repeat(5 - (player.playoff_sos || 3))}</i>
                        </span>
                      </>
                    ) : (
                      <>
                        {showPosColumn ? <span className="fantasy-pos-rank">{player.pos_rank}</span> : null}
                        {isWeekly ? <span>{player.opponent}</span> : null}
                        {isWeekly ? (
                          player.stars ? (
                            <span className="fantasy-stars" title={`${player.stars}/5 matchup`}>
                              {"★".repeat(player.stars)}
                              <i>{"★".repeat(5 - player.stars)}</i>
                            </span>
                          ) : <span>—</span>
                        ) : null}
                        {isRos ? (
                          <span className="fantasy-stars" title={`${player.season_sos || 3}/5 season SOS`}>
                            {"★".repeat(player.season_sos || 3)}
                            <i>{"★".repeat(5 - (player.season_sos || 3))}</i>
                          </span>
                        ) : null}
                        {isRos ? (
                          <span className="fantasy-stars" title={`${player.playoff_sos || 3}/5 playoff SOS (Wks 15-17)`}>
                            {"★".repeat(player.playoff_sos || 3)}
                            <i>{"★".repeat(5 - (player.playoff_sos || 3))}</i>
                          </span>
                        ) : null}
                        <b>{displayedValue(player, index)}</b>
                        <i
                          className={player.trend > 0 ? "up" : player.trend < 0 ? "down" : ""}
                          title={trendTitle(player)}
                        >
                          {player.trend > 0 ? `↑ ${player.trend}` : player.trend < 0 ? `↓ ${Math.abs(player.trend)}` : "—"}
                        </i>
                      </>
                    )}
                  </button>
                ))}
              </div>
              <div className="fantasy-board-footer">
                <span>Showing {filteredPlayers.length} of {totalPlayers} players</span>
                {filteredPlayers.length < totalPlayers ? (
                  <button onClick={() => setVisibleCount(visibleCount + 50)}>Load more rankings</button>
                ) : <span />}
                <b>{isWeekly ? (weeklyMode === "final" ? "Final results" : "Projected") : isRos ? "Projected ROS" : isAdp ? "Preseason ADP" : "Updated 8 min ago"}</b>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
