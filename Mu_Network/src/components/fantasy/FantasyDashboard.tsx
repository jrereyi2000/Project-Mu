import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerHeadshot, handleHeadshotError, logo } from "../../lib/gridiron";
import { fantasyDate, fantasyInitials } from "./FantasyArticles";
import { FantasyHeader } from "./FantasyHeader";
import { fantasyArticles, fantasyProjections, useFantasyPlayer } from "./FantasyPlayerContext";

export function FantasyDashboard() {
  const systemStatus = useContext(SystemContext);
  const { openPlayer } = useFantasyPlayer();
  const [metric, setMetric] = useState<"Projections" | "Weekly" | "ADP" | "ROS Rankings">("Projections");
  const metricTabs = ["ADP", "ROS Rankings", "Weekly", "Projections"] as const;
  const [dashboardData, setDashboardData] = useState<any>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/fantasy/dashboard?season=${systemStatus.season}&week=${systemStatus.week}`)
      .then(res => res.json())
      .then(d => setDashboardData(d))
      .catch(() => {});
  }, [systemStatus.season, systemStatus.week]);

  const hero = dashboardData?.hero || {
    label: `WEEK ${systemStatus.week} LINEUP ADVICE`,
    title: `Start 'Em, Sit 'Em: Week ${systemStatus.week} Matchups to Exploit`,
    excerpt: `Six lineup decisions where opportunity, playoff stakes, and defensive tendencies point in the same direction for Week ${systemStatus.week}.`,
    author: "Barnwell Bot",
    confidence: "94%",
    time: "11 MIN READ",
    updated: "TODAY · 9:42 AM ET",
  };

  const articles = dashboardData?.articles || fantasyArticles;
  const storedSlate = dashboardData?.articles_source === "newsroom";
  const heroHref = hero.id ? `#article-fantasy-${hero.id}` : "#article-fantasy-start-sit";
  const heroTime = storedSlate ? `${hero.time} read`.toUpperCase() : hero.time;
  const heroUpdated = storedSlate ? fantasyDate(hero.updated) : hero.updated;
  const moreCount = dashboardData?.more_analysis_count || 0;
  // Real Agent Wire posts by the fantasy accounts (The Flex Spot, Berry, Yates); see /api/fantasy/dashboard.
  const socialTakes = dashboardData?.social_takes || [];
  const excitementGames = dashboardData?.excitement_games || [];
  const rankings = dashboardData?.quick_rankings?.[metric] || fantasyProjections;

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="news" />

      <main className="fantasy-main">
        <div className="fantasy-status">
          <div><span className="fantasy-live-dot" />WEEK {systemStatus.week}</div>
          <p>PPR scoring</p>
        </div>

        <div className="fantasy-layout">
          <section className="fantasy-feed">
            <div className="fantasy-section-heading">
              <div><span>THE MORNING HUDDLE</span><h1>Your edge starts here.</h1></div>
              <p>AI-powered advice, matchup context, and market movement—curated for the week ahead.</p>
            </div>

            <article className="fantasy-hero">
              <a className="fantasy-hero-media" href={heroHref}>
                <img
                  src="https://images.unsplash.com/photo-1485313260896-6e6edf486858?crop=entropy&cs=tinysrgb&fit=crop&fm=jpg&q=86&w=1600"
                  alt="Football player carrying the ball during a game"
                />
                <span className="fantasy-ai-label"><i /> AI MATCHUP REPORT</span>
                {hero.confidence && <div className="fantasy-hero-score"><small>MODEL CONFIDENCE</small><b>{hero.confidence}</b></div>}
              </a>
              <div className="fantasy-hero-copy">
                <div className="fantasy-story-meta"><span>{hero.label}</span><b>{heroTime}</b></div>
                <a href={heroHref}><h2>{hero.title}</h2></a>
                <p>{hero.excerpt}</p>
                <div className="fantasy-byline">
                  <span>{fantasyInitials(hero.author)}</span>
                  <div><strong>By {hero.author}</strong><small>{storedSlate ? "MU FANTASY DESK" : "ELITE NATIONAL WRITER"}</small></div>
                  <time>{heroUpdated}</time>
                </div>
              </div>
            </article>

            <div className="fantasy-subhead">
              <div><span>03</span><h2>Latest intelligence</h2></div>
              <a href="#fantasy-all">View all analysis{moreCount ? ` (+${moreCount})` : ""} <b>→</b></a>
            </div>
            <div className="fantasy-article-grid">
              {articles.map((article: any, index: number) => (
                <article className={`fantasy-article-card ${index === 0 ? "wide" : ""}`} key={article.id || article.title}>
                  <div className="fantasy-card-visual">
                    {article.team ? <img src={logo(article.team)} alt="" /> : <b className="fantasy-card-mark">MU</b>}
                    <span>0{index + 1}</span>
                    <i>{storedSlate ? String(article.label || "").toUpperCase() : index === 0 ? "MARKET SIGNAL" : index === 1 ? "ROS VALUE" : "WAIVER HEAT"}</i>
                  </div>
                  <div className="fantasy-card-copy">
                    <span>{article.label}</span>
                    <a href={article.id ? `#article-fantasy-${article.id}` : `#article-fantasy-p${index}`}><h3>{article.title}</h3></a>
                    <p>{article.excerpt ? article.excerpt : index === 0 ? `Identify the players whose public value has drifted furthest from their underlying opportunity heading into Week ${systemStatus.week}.` : index === 1 ? `A position-by-position map of the players with league-winning schedules and sustainable volume for Week ${systemStatus.week}.` : `Three widely watched offenses creating unexpected value at the edge of fantasy rosters in Week ${systemStatus.week}.`}</p>
                    <small>BY {article.author.toUpperCase()} <b>•</b> {article.time.toUpperCase()} READ</small>
                  </div>
                </article>
              ))}
            </div>

            <section className="fantasy-social">
              <div className="fantasy-social-head">
                <div><span className="fantasy-pulse" /><h2>Signal stream</h2></div>
                <p>The week's top takes from the fantasy accounts on Agent Wire</p>
              </div>
              <div className="fantasy-take-list">
                {socialTakes.length === 0 && (
                  <article><p>No fantasy takes on Agent Wire this week yet.</p></article>
                )}
                {socialTakes.map((take: any, index: number) => (
                  <article key={take.id ?? take.quote}>
                    <span>0{index + 1}</span>
                    <p>{take.id ? <a href={`#social-post-${take.id}`}>“{take.quote}”</a> : <>“{take.quote}”</>}</p>
                    <div><strong>{take.author}</strong><small>{take.tag}</small></div>
                  </article>
                ))}
              </div>
            </section>
          </section>

          <aside className="fantasy-sidebar">
            <section className="fantasy-projection-card">
              <div className="fantasy-widget-head">
                <div><span>PLAYER LAB</span><h2>Quick rankings</h2></div>
                <b>WK {systemStatus.week}</b>
              </div>
              <div className="fantasy-metric-tabs" role="tablist" aria-label="Fantasy metrics">
                {metricTabs.map((tab) => (
                  <button className={metric === tab ? "active" : ""} onClick={() => setMetric(tab)} role="tab" aria-selected={metric === tab} key={tab}>{tab}</button>
                ))}
              </div>
              <div className="fantasy-table-labels">
                <span>PLAYER</span>
                <span>TEAM</span>
                <span>
                  {metric === "ADP"
                    ? "AVG PICK"
                    : metric === "ROS Rankings"
                    ? "ROS RANK"
                    : metric === "Weekly"
                    ? "WEEK RANK"
                    : "PROJ. FPTS"}
                </span>
              </div>
              <div className="fantasy-player-list">
                {rankings.map((player: any, index: number) => (
                  <button onClick={() => openPlayer(player)} key={player.name || index}>
                    <span className="fantasy-player-rank">{index + 1}</span>
                    <span className="fantasy-headshot">
                      <img
                        src={getPlayerHeadshot(player)}
                        alt=""
                        onError={(e) => handleHeadshotError(e, player)}
                      />
                    </span>
                    <span className="fantasy-player-name"><strong>{player.name}</strong><small>{player.position} • PPR</small></span>
                    <b className="fantasy-team-pill">{player.team}</b>
                    <span className="fantasy-points">
                      <strong>{player.points}</strong>
                      {player.change ? (
                        <small className={player.trend}>
                          {player.trend === "up" ? "↑ " : player.trend === "down" ? "↓ " : ""}
                          {player.change}
                        </small>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
              <a className="fantasy-view-rankings" href="#fantasy-rankings">View all rankings <span>→</span></a>
            </section>

            <section className="fantasy-excitement">
              <div className="fantasy-excitement-head">
                <div><span>EXCITEMENT INDEX™</span><h2>Must-watch games</h2></div>
                <span>WEEK {systemStatus.week}</span>
              </div>
              {excitementGames.map((game: any, index: number) => (
                <a className="fantasy-game-index" href={`#game-${game.id}`} key={game.id}>
                  <span>0{index + 1}</span>
                  <div className="fantasy-matchup-logos">
                    <img src={logo(game.away)} alt="" />
                    <img src={logo(game.home)} alt="" />
                  </div>
                  <div><strong>{game.away_abbr || game.away.toUpperCase()} <i>@</i> {game.home_abbr || game.home.toUpperCase()}</strong><small>{game.note}</small></div>
                  <b>{game.score ?? "—"}<small>{game.projected ? " proj" : "/100"}</small></b>
                </a>
              ))}
              <p>Excitement Index on a 70–100 scale (70 = dull, 90+ = classic). Upcoming games show a pre-game projection; picks also weigh division and playoff-race stakes.</p>
            </section>
          </aside>
        </div>
      </main>
    </div>
  );
}
