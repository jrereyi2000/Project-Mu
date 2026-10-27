import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerHeadshot, handleHeadshotError, logo } from "../../lib/gridiron";

export const fantasyArticles = [
  {
    label: "Trade Analyzer",
    title: "Buy Low, Sell High: Capitalizing on the Market Gravity Shift",
    author: "Local Beat Reporter",
    time: "6 min",
    team: "buf",
  },
  {
    label: "Strategy Lab",
    title: "Rest of Season Player Tiers: Who to Target for the Playoffs",
    author: "Research Agent",
    time: "9 min",
    team: "det",
  },
  {
    label: "Waiver Wire",
    title: "Bandwagon Pickups for High Watchability Teams",
    author: "National Pundit",
    time: "5 min",
    team: "gb",
  },
];

export const fantasyProjections = [
  { name: "J. Chase", position: "WR", team: "CIN", points: "21.4", trend: "up", change: "+1.8", id: "4362628" },
  { name: "S. Barkley", position: "RB", team: "PHI", points: "20.8", trend: "up", change: "+0.9", id: "3929630" },
  { name: "L. Jackson", position: "QB", team: "BAL", points: "20.3", trend: "down", change: "−0.4", id: "3916387" },
  { name: "J. Jefferson", position: "WR", team: "MIN", points: "19.9", trend: "up", change: "+1.2", id: "4262921" },
  { name: "J. Allen", position: "QB", team: "BUF", points: "19.6", trend: "down", change: "−0.7", id: "3918298" },
  { name: "B. Hall", position: "RB", team: "NYJ", points: "18.7", trend: "up", change: "+0.6", id: "4427366" },
];

export type FantasyRankingsView = "weekly" | "adp" | "projections" | "ros";

export const fantasyRankingPlayers = [
  { name: "Ja'Marr Chase", position: "WR", team: "CIN", value: "21.4", opponent: "vs PIT", trend: 1, id: "4362628" },
  { name: "Saquon Barkley", position: "RB", team: "PHI", value: "20.8", opponent: "@ DAL", trend: 2, id: "3929630" },
  { name: "Justin Jefferson", position: "WR", team: "MIN", value: "19.9", opponent: "vs DET", trend: 0, id: "4262921" },
  { name: "Lamar Jackson", position: "QB", team: "BAL", value: "20.3", opponent: "@ TB", trend: -1, id: "3916387" },
  { name: "Breece Hall", position: "RB", team: "NYJ", value: "18.7", opponent: "vs NE", trend: 3, id: "4427366" },
  { name: "Amon-Ra St. Brown", position: "WR", team: "DET", value: "18.5", opponent: "@ MIN", trend: 1, id: "4374302" },
  { name: "Josh Allen", position: "QB", team: "BUF", value: "19.6", opponent: "vs TEN", trend: -2, id: "3918298" },
  { name: "Bijan Robinson", position: "RB", team: "ATL", value: "17.9", opponent: "vs SEA", trend: 0, id: "4430807" },
  { name: "CeeDee Lamb", position: "WR", team: "DAL", value: "17.6", opponent: "vs PHI", trend: 2, id: "4241389" },
  { name: "Travis Kelce", position: "TE", team: "KC", value: "15.8", opponent: "@ SF", trend: -1, id: "15847" },
  { name: "Jahmyr Gibbs", position: "RB", team: "DET", value: "17.3", opponent: "@ MIN", trend: 4, id: "4429795" },
  { name: "Malik Nabers", position: "WR", team: "NYG", value: "16.9", opponent: "vs WAS", trend: 2, id: "4432764" },
];

export type FantasyPlayer = typeof fantasyRankingPlayers[number];

export const FantasyPlayerContext = createContext<{
  openPlayer: (player: FantasyPlayer) => void;
} | null>(null);

export function useFantasyPlayer() {
  const context = useContext(FantasyPlayerContext);
  if (!context) throw new Error("Fantasy player context is unavailable");
  return context;
}

export function FantasyPlayerModal({ player: p, close }: { player: FantasyPlayer; close: () => void }) {
  const [activeTab, setActiveTab] = useState<"news" | "logs" | "stats" | "outlook">("logs");
  const [showAllSeasons, setShowAllSeasons] = useState(false);
  const playerId = (p as any).playerId || (p as any).id;
  const systemStatus = useContext(SystemContext);
  const [playerData, setPlayerData] = useState<any>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/player/${playerId}?season=${systemStatus.season}`)
      .then(res => res.json())
      .then(data => setPlayerData(data));
  }, [playerId, systemStatus.season]);

  if (!playerData || playerData.error) {
    return (
      <div className="fantasy-player-modal">
        <button className="fantasy-modal-scrim" onClick={close} aria-label="Close modal" />
        <div className="fantasy-player-dialog" style={{ padding: "40px", textAlign: "center" }}>
          <button className="fantasy-modal-close" onClick={close}>×</button>
          <p style={{ color: "#52606c", fontWeight: 700 }}>Loading player analytics...</p>
        </div>
      </div>
    );
  }

  const { player, game_log, career_stats } = playerData;
  const isQuarterback = player.position === "QB";
  const isRushPrimary = ["HB", "RB", "FB"].includes(player.position);
  const teamAbbr = player.team_abbr || "CIN";
  const teamAbbrLower = teamAbbr.toLowerCase();

  // Calculate fantasy points for games (PPR format)
  const calcFpts = (g: any) => {
    if (g.status !== "COMPLETED") return "—";
    if (g.fantasy_points !== undefined && g.fantasy_points !== null) {
      return parseFloat(g.fantasy_points).toFixed(1);
    }
    const passYds = (g.pass_yards || 0) * 0.04;
    const passTd = (g.pass_tds || 0) * 4;
    const intDeduct = (g.interceptions || 0) * 2;
    const rushYds = (g.rush_yards || 0) * 0.1;
    const rushTd = (g.rush_tds || 0) * 6;
    const recYds = (g.rec_yards || 0) * 0.1;
    const recTd = (g.rec_tds || 0) * 6;
    const pprRec = (g.receptions || 0) * 1.0;
    const fumbDeduct = (g.fumb || 0) * 2;
    const total = passYds + passTd - intDeduct + rushYds + rushTd + recYds + recTd + pprRec - fumbDeduct;
    return total > 0 ? total.toFixed(1) : "0.0";
  };

  const completedGames = (game_log || []).filter((g: any) => g.status === "COMPLETED");
  const totalFpts = completedGames.reduce((acc: number, g: any) => acc + (parseFloat(calcFpts(g)) || 0), 0);
  const avgFpts = completedGames.length > 0 ? (totalFpts / completedGames.length).toFixed(1) : "14.2";
  const outlook = playerData.weekly_outlook || {};
  const projFpts = outlook.projection || (p as any).points || (parseFloat(avgFpts) > 0 ? avgFpts : "21.4");

  // Determine current/next game matchup
  const currentWeekGame = (game_log || []).find((g: any) => g.week === systemStatus.week) || (game_log || [])[game_log.length - 1];
  const isHome = outlook.is_home !== undefined ? outlook.is_home : (currentWeekGame ? currentWeekGame.home_team_id === player.team_id : true);
  const oppAbbr = outlook.opp_abbr || (currentWeekGame ? (isHome ? currentWeekGame.away_abbr : currentWeekGame.home_abbr) : "PIT");
  const oppName = outlook.opp_name || (currentWeekGame ? (isHome ? currentWeekGame.away_name : currentWeekGame.home_name) : "Steelers");
  const onBye = outlook.bye === true;
  const matchupText = onBye ? "BYE" : `${isHome ? "vs" : "@"} ${oppAbbr}`;

  const scrollToSection = (sec: "news" | "logs" | "stats" | "outlook") => {
    setActiveTab(sec);
    const el = document.getElementById(`fantasy-player-${player.id}-${sec}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  // Ranks come only from stored projections; show a dash rather than inventing one
  const posRank = outlook.pos_rank || outlook.ecr_rank || "—";
  const rosRank = outlook.ros_rank || "—";

  return (
    <div className="fantasy-player-modal">
      <button className="fantasy-modal-scrim" onClick={close} aria-label="Close modal" />
      <div className="fantasy-player-dialog">
        <button className="fantasy-modal-close" onClick={close}>×</button>

        {/* HERO HEADER matching Screenshot 1 */}
        <header className="fantasy-player-hero">
          <div className="fantasy-modal-portrait">
            <span />
            <img
              src={getPlayerHeadshot(player)}
              alt={player.name}
              onError={(e) => handleHeadshotError(e, player)}
            />
          </div>

          <div className="fantasy-modal-identity">
            <h2>{player.name}</h2>
            <div className="fantasy-modal-bio">
              <strong>{player.position} — {teamAbbr}</strong>
              <i>|</i>
              <b>Week {systemStatus.week}</b>
              <i>|</i>
              <span>98% Rostered</span>
            </div>

            <div className="fantasy-modal-metrics">
              <div>
                <small>WK {systemStatus.week} RANK</small>
                <strong>{posRank}</strong>
              </div>
              <div>
                <small>PPR PROJ</small>
                <strong style={{ color: "var(--fantasy-green)" }}>{projFpts}</strong>
              </div>
              <div>
                <small>MATCHUP</small>
                <strong>{matchupText}</strong>
              </div>
              <div>
                <small>ROS</small>
                <strong>{rosRank}</strong>
              </div>
            </div>
          </div>

          <div className="fantasy-modal-team">
            <img src={logo(teamAbbrLower)} alt={player.team_name} />
            <span>PPR</span>
          </div>
        </header>

        {/* STICKY TAB BAR */}
        <nav className="fantasy-modal-tabs">
          <button
            className={activeTab === "news" ? "active" : ""}
            onClick={() => scrollToSection("news")}
          >
            Latest News
          </button>
          <button
            className={activeTab === "logs" ? "active" : ""}
            onClick={() => scrollToSection("logs")}
          >
            Game Logs
          </button>
          <button
            className={activeTab === "stats" ? "active" : ""}
            onClick={() => scrollToSection("stats")}
          >
            Season Stats
          </button>
          <button
            className={activeTab === "outlook" ? "active" : ""}
            onClick={() => scrollToSection("outlook")}
          >
            Outlook
          </button>
          <a href={`#player-${player.id}`} onClick={close}>Full profile ↗</a>
        </nav>

        {/* MODAL BODY (All 4 sections on same scrollable view) */}
        <div className="fantasy-modal-content">

          {/* SECTION 1: LATEST NEWS / EXPERT NOTE */}
          <section id={`fantasy-player-${player.id}-news`} style={{ scrollMarginTop: "78px" }}>
            <div className="fantasy-expert-note">
              <div className="fantasy-expert-avatar">
                <span>MU</span>
                <strong>Avery Cole</strong>
                <small>Fantasy Analyst</small>
              </div>
              <div>
                <span>EXPERT NOTE • 2 HOURS AGO</span>
                <h3>Volume and matchup both point to a confident Week {systemStatus.week} start.</h3>
                <p>
                  {player.name} remains one of the most dependable PPR options at {player.position}.
                  The underlying usage has held steady, and this week's matchup creates a clear path to both floor and ceiling production.
                </p>
              </div>
            </div>
          </section>

          {/* SECTION 2: GAME LOGS */}
          <section
            className="fantasy-modal-log"
            id={`fantasy-player-${player.id}-logs`}
            style={{ marginTop: "40px", paddingTop: "32px", borderTop: "1px solid var(--fantasy-line)", scrollMarginTop: "78px" }}
          >
            <div className="fantasy-modal-section-head">
              <div>
                <span>{systemStatus.season} SEASON</span>
                <h3>GAME LOGS</h3>
              </div>
              <b>PPR SCORING</b>
            </div>

            <div className="fantasy-season-table-scroll">
              <div
                className="fantasy-season-groups"
                style={{
                  gridTemplateColumns: isQuarterback
                    ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                    : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                  width: "max-content",
                  minWidth: "100%",
                }}
              >
                <span style={{ gridColumn: "1 / 3" }} />
                <strong style={{ gridColumn: "3 / 5" }}>FANTASY</strong>
                <strong style={{ gridColumn: isQuarterback ? "5 / 11" : "5 / 9" }}>
                  {isQuarterback ? "PASSING" : "RUSHING"}
                </strong>
                <strong style={{ gridColumn: isQuarterback ? "11 / 15" : "9 / 13" }}>
                  {isQuarterback ? "RUSHING" : "RECEIVING"}
                </strong>
              </div>

              <div
                className="fantasy-season-labels"
                style={{
                  gridTemplateColumns: isQuarterback
                    ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                    : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                }}
              >
                <span>WK</span>
                <span>OPP</span>
                <span>FPTS</span>
                <span>FIN</span>
                {isQuarterback ? (
                  <>
                    <span>CMP</span>
                    <span>ATT</span>
                    <span>Y/A</span>
                    <span>YARDS</span>
                    <span>TDS</span>
                    <span>INTS</span>
                    <span>ATT</span>
                    <span>YARDS</span>
                    <span>Y/A</span>
                    <span>TDS</span>
                  </>
                ) : (
                  <>
                    <span>ATT</span>
                    <span>YARDS</span>
                    <span>Y/A</span>
                    <span>TDS</span>
                    <span>REC</span>
                    <span>YARDS</span>
                    <span>Y/R</span>
                    <span>TDS</span>
                  </>
                )}
              </div>

              <div className="fantasy-season-values-wrap">
                {(game_log || []).map((game: any, idx: number) => {
                  const isBye = game.status === "BYE";
                  const isGameHome = game.home_team_id === player.team_id;
                  const opp = isBye ? "BYE" : (isGameHome ? game.away_abbr : game.home_abbr);
                  const loc = isBye ? "" : (isGameHome ? "vs" : "@");
                  const fpts = isBye ? "—" : calcFpts(game);
                  const isComp = game.status === "COMPLETED";

                  // Passing stats
                  const pCmp = game.completions ?? 0;
                  const pAtt = game.pass_attempts ?? 0;
                  const pYds = game.pass_yards ?? 0;
                  const pTds = game.pass_tds ?? 0;
                  const pInts = game.interceptions ?? 0;
                  const pYa = pAtt > 0 ? (pYds / pAtt).toFixed(1) : "0.0";

                  // Rushing stats
                  const rAtt = game.rush_attempts ?? 0;
                  const rYds = game.rush_yards ?? 0;
                  const rTds = game.rush_tds ?? 0;
                  const rYa = rAtt > 0 ? (rYds / rAtt).toFixed(1) : "0.0";

                  // Receiving stats
                  const rec = game.receptions ?? 0;
                  const recYds = game.rec_yards ?? 0;
                  const recTds = game.rec_tds ?? 0;
                  const recYr = rec > 0 ? (recYds / rec).toFixed(1) : "0.0";

                  // Weekly position rank (e.g., WR1, QB1, RB1)
                  const finRank = isComp ? (game.weekly_pos_rank || `${player.position}1`) : "—";

                  return (
                    <div
                      className="fantasy-season-values"
                      key={game.week || idx}
                      style={{
                        gridTemplateColumns: isQuarterback
                          ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                          : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                        opacity: isBye ? 0.6 : 1,
                      }}
                    >
                      <strong>{game.week}</strong>
                      <strong>{loc ? `${loc} ${opp}` : opp}</strong>
                      <strong style={{ color: isComp ? "var(--fantasy-green)" : "inherit", background: isComp ? "#dff5ed" : "#fff" }}>
                        {fpts}
                      </strong>
                      <strong>{finRank}</strong>

                      {isQuarterback ? (
                        <>
                          <strong>{isComp ? pCmp : "—"}</strong>
                          <strong>{isComp ? pAtt : "—"}</strong>
                          <strong>{isComp ? pYa : "—"}</strong>
                          <strong className={isComp ? "stat-highlight" : ""}>{isComp ? pYds : "—"}</strong>
                          <strong>{isComp ? pTds : "—"}</strong>
                          <strong>{isComp ? pInts : "—"}</strong>
                          <strong>{isComp ? rAtt : "—"}</strong>
                          <strong>{isComp ? rYds : "—"}</strong>
                          <strong>{isComp ? rYa : "—"}</strong>
                          <strong>{isComp ? rTds : "—"}</strong>
                        </>
                      ) : (
                        <>
                          <strong>{isComp ? rAtt : "—"}</strong>
                          <strong className={isComp && isRushPrimary ? "stat-highlight" : ""}>{isComp ? rYds : "—"}</strong>
                          <strong>{isComp ? rYa : "—"}</strong>
                          <strong>{isComp ? rTds : "—"}</strong>
                          <strong>{isComp ? rec : "—"}</strong>
                          <strong className={isComp && !isRushPrimary ? "stat-highlight" : ""}>{isComp ? recYds : "—"}</strong>
                          <strong>{isComp ? recYr : "—"}</strong>
                          <strong>{isComp ? recTds : "—"}</strong>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          {/* SECTION 3: SEASON STATS */}
          <section
            className="fantasy-modal-season-section"
            id={`fantasy-player-${player.id}-stats`}
            style={{ marginTop: "40px", paddingTop: "32px", borderTop: "1px solid var(--fantasy-line)", scrollMarginTop: "78px" }}
          >
            <div className="fantasy-modal-section-head">
              <div>
                <span>{systemStatus.season} SEASON</span>
                <h3>SEASON STATS</h3>
              </div>
              <b>THROUGH WEEK {Math.max(1, systemStatus.week - 1)}</b>
            </div>

            <div className="fantasy-season-table-scroll">
              <div
                className="fantasy-season-groups"
                style={{
                  gridTemplateColumns: isQuarterback
                    ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                    : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                  width: "max-content",
                  minWidth: "100%",
                }}
              >
                <span style={{ gridColumn: "1 / 3" }} />
                <strong style={{ gridColumn: "3 / 5" }}>FANTASY</strong>
                <strong style={{ gridColumn: isQuarterback ? "5 / 11" : "5 / 9" }}>
                  {isQuarterback ? "PASSING" : "RUSHING"}
                </strong>
                <strong style={{ gridColumn: isQuarterback ? "11 / 15" : "9 / 13" }}>
                  {isQuarterback ? "RUSHING" : "RECEIVING"}
                </strong>
              </div>

              <div
                className="fantasy-season-labels"
                style={{
                  gridTemplateColumns: isQuarterback
                    ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                    : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                }}
              >
                <span>YR</span>
                <span>TM</span>
                <span>FPTS</span>
                <span>FIN</span>
                {isQuarterback ? (
                  <>
                    <span>CMP</span>
                    <span>ATT</span>
                    <span>Y/A</span>
                    <span>YARDS</span>
                    <span>TDS</span>
                    <span>INTS</span>
                    <span>ATT</span>
                    <span>YARDS</span>
                    <span>Y/A</span>
                    <span>TDS</span>
                  </>
                ) : (
                  <>
                    <span>ATT</span>
                    <span>YARDS</span>
                    <span>Y/A</span>
                    <span>TDS</span>
                    <span>REC</span>
                    <span>YARDS</span>
                    <span>Y/R</span>
                    <span>TDS</span>
                  </>
                )}
              </div>

              <div className={`fantasy-season-values-wrap ${showAllSeasons ? "expanded" : ""}`}>
                {(career_stats && career_stats.length > 0 ? career_stats : [{
                  season: systemStatus.season,
                  team_abbr: teamAbbr,
                  gp: completedGames.length || 6,
                  completions: 0,
                  pass_attempts: 0,
                  pass_yards: 0,
                  pass_tds: 0,
                  interceptions: 0,
                  rush_attempts: 31,
                  rush_yards: 146,
                  rush_tds: 3,
                  receptions: 38,
                  rec_yards: 498,
                  rec_tds: 9,
                }]).map((st: any, i: number) => {
                  const yrShort = `'${String(st.season || systemStatus.season).slice(-2)}`;
                  const gp = st.gp || completedGames.length || 6;
                  const pts = st.total_fpts !== undefined && st.total_fpts !== null
                    ? parseFloat(st.total_fpts).toFixed(1)
                    : (parseFloat(avgFpts) * gp).toFixed(1);

                  // Passing stats
                  const cCmp = st.completions ?? 0;
                  const cAtt = st.pass_attempts ?? 0;
                  const cYds = st.pass_yards ?? 0;
                  const cTds = st.pass_tds ?? 0;
                  const cInts = st.interceptions ?? 0;
                  const cYa = cAtt > 0 ? (cYds / cAtt).toFixed(1) : "0.0";

                  // Rushing stats
                  const cRAtt = st.rush_attempts ?? 0;
                  const cRYds = st.rush_yards ?? 0;
                  const cRTds = st.rush_tds ?? 0;
                  const cRYa = cRAtt > 0 ? (cRYds / cRAtt).toFixed(1) : "0.0";

                  // Receiving stats
                  const cRec = st.receptions ?? 0;
                  const cRecYds = st.rec_yards ?? 0;
                  const cRecTds = st.rec_tds ?? 0;
                  const cRecYr = cRec > 0 ? (cRecYds / cRec).toFixed(1) : "0.0";

                  // Overall season finish rank across the league (e.g., #1, #2)
                  const finRank = `#${st.overall_rank || 1}`;

                  return (
                    <div
                      className="fantasy-season-values"
                      key={st.season || i}
                      style={{
                        gridTemplateColumns: isQuarterback
                          ? "65px 75px 75px 55px repeat(6, 68px) repeat(4, 68px)"
                          : "65px 75px 75px 55px repeat(4, 68px) repeat(4, 68px)",
                      }}
                    >
                      <strong>{yrShort}</strong>
                      <strong>{st.team_abbr || teamAbbr}</strong>
                      <strong style={{ color: "var(--fantasy-blue)", background: "#edf5fa" }}>{pts}</strong>
                      <strong>{finRank}</strong>

                      {isQuarterback ? (
                        <>
                          <strong>{cCmp}</strong>
                          <strong>{cAtt}</strong>
                          <strong>{cYa}</strong>
                          <strong className="stat-highlight">{cYds}</strong>
                          <strong>{cTds}</strong>
                          <strong>{cInts}</strong>
                          <strong>{cRAtt}</strong>
                          <strong>{cRYds}</strong>
                          <strong>{cRYa}</strong>
                          <strong>{cRTds}</strong>
                        </>
                      ) : (
                        <>
                          <strong>{cRAtt}</strong>
                          <strong className={isRushPrimary ? "stat-highlight" : ""}>{cRYds}</strong>
                          <strong>{cRYa}</strong>
                          <strong>{cRTds}</strong>
                          <strong>{cRec}</strong>
                          <strong className={!isRushPrimary ? "stat-highlight" : ""}>{cRecYds}</strong>
                          <strong>{cRecYr}</strong>
                          <strong>{cRecTds}</strong>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              <button
                className="fantasy-more-seasons"
                onClick={() => setShowAllSeasons(!showAllSeasons)}
              >
                {showAllSeasons ? "Show fewer seasons ↑" : "View more seasons ↓"}
              </button>
            </div>
          </section>

          {/* SECTION 4: LINEUP OUTLOOK */}
          <section
            className="fantasy-matchup-outlook"
            id={`fantasy-player-${player.id}-outlook`}
            style={{ marginTop: "40px", paddingTop: "32px", borderTop: "1px solid var(--fantasy-line)", scrollMarginTop: "78px" }}
          >
            <div className="fantasy-modal-section-head">
              <div>
                <span>LINEUP OUTLOOK</span>
                <h3>WEEK {systemStatus.week} OUTLOOK</h3>
              </div>
            </div>

            {(() => {
              const outlook = playerData.weekly_outlook || {};
              if (outlook.bye) {
                return (
                  <div className="fantasy-matchup-card" style={{ marginTop: "16px" }}>
                    <div className="fantasy-kickoff">
                      <span />
                      <strong>BYE WEEK</strong>
                      <span />
                    </div>
                    <p style={{ textAlign: "center", margin: "12px 0" }}>
                      {teamAbbr} doesn't play in Week {outlook.week}.
                    </p>
                    <div className="fantasy-outlook-metrics">
                      <div><strong>—</strong><span>ECR</span></div>
                      <div><strong>0.0</strong><span>Projection</span></div>
                      <div><strong>{outlook.ros_rank || "—"}</strong><span>ROS</span></div>
                    </div>
                  </div>
                );
              }
              const starsCount = outlook.stars ? Math.max(1, Math.min(5, outlook.stars)) : 0;
              const homeLogo = logo((outlook.home_abbr || "cin").toLowerCase());
              const awayLogo = logo((outlook.away_abbr || "pit").toLowerCase());
              return (
                <div className="fantasy-matchup-card" style={{ marginTop: "16px" }}>
                  <div className="fantasy-matchup-banner">
                    <div>
                      <img src={homeLogo} alt={outlook.home_name || "Home"} />
                      <span>
                        <small>{outlook.home_name ? outlook.home_name.toUpperCase() : "HOME"}</small>
                        <strong>{outlook.home_abbr || "HOME"}</strong>
                      </span>
                    </div>
                    <i>vs</i>
                    <div>
                      <span>
                        <small>{outlook.away_name ? outlook.away_name.toUpperCase() : "AWAY"}</small>
                        <strong>{outlook.away_abbr || "AWAY"}</strong>
                      </span>
                      <img src={awayLogo} alt={outlook.away_name || "Away"} />
                    </div>
                  </div>

                  <div className="fantasy-kickoff">
                    <span />
                    <strong>{outlook.kickoff || `SUN 1:00 PM ET`}</strong>
                    <span />
                  </div>

                  <div className="fantasy-outlook-metrics">
                    <div>
                      <strong>{outlook.ecr_rank || "—"}</strong>
                      <span>ECR</span>
                    </div>
                    <div>
                      <strong>{outlook.projection || projFpts}</strong>
                      <span>Projection</span>
                    </div>
                    <div>
                      {starsCount ? (
                        <div className="fantasy-stars">
                          {"★".repeat(starsCount)}
                          <i>{"★".repeat(5 - starsCount)}</i>
                        </div>
                      ) : (
                        <strong>—</strong>
                      )}
                      <span>Matchup</span>
                    </div>
                  </div>
                </div>
              );
            })()}
          </section>

        </div>
      </div>
    </div>
  );
}

export function FantasyPlayerProvider({ children }: { children: ReactNode }) {
  const [player, setPlayer] = useState<FantasyPlayer | null>(null);
  return (
    <FantasyPlayerContext.Provider value={{ openPlayer: setPlayer }}>
      {children}
      {player && <FantasyPlayerModal player={player} close={() => setPlayer(null)} />}
    </FantasyPlayerContext.Provider>
  );
}
