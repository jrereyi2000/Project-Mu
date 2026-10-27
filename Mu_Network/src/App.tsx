import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { API_BASE } from "./api";
import DebugPage from "./DebugPage";
import { DraftPage } from "./DraftPage";
import { NapperPage } from "./NapperPage";
import AwardsPage from "./components/history/AwardsPage";
import RecordBookPage from "./components/history/RecordBookPage";
import { SystemContext } from "./context/SystemContext";
export { SystemContext };
import {
  type Team,
  teams,
  Chevron,
} from "./lib/gridiron";
import { ScoreTicker } from "./components/shell/ScoreTicker";
import { MegaMenu } from "./components/shell/MegaMenu";
import { StatsDropdown } from "./components/shell/StatsDropdown";
import { GlobalSearchModal } from "./components/shell/GlobalSearchModal";
import {
  SocialFeedPage,
  SocialPostDetailPage,
  SocialProfilePage,
  SocialTeamHubsPage,
  SocialTeamFeedPage,
  SocialTrendPage,
  SocialExplorePage,
} from "./components/social/SocialPages";
import { FantasyPlayerProvider } from "./components/fantasy/FantasyPlayerContext";
import { FantasyRankingsPage } from "./components/fantasy/FantasyRankingsPage";
import { FantasyDashboard } from "./components/fantasy/FantasyDashboard";
import { FantasyArticlePage, FantasyAllPage } from "./components/fantasy/FantasyArticles";

import { TeamPage } from "./components/team/TeamPage";
import { SchedulePage, TeamSchedulePage } from "./components/league/SchedulePage";
import { GamePage } from "./components/league/GamePage";
import { PlayerPage } from "./components/league/PlayerPage";
import { StatsPage } from "./components/league/StatsPage";
import { StandingsPage } from "./components/league/StandingsPage";
import { PowerRankingsPage, type PowerRankingsResponse } from "./components/newsroom/PowerRankingsPage";
import { ArticlePage } from "./components/newsroom/ArticlePage";
import { AuthorPage } from "./components/newsroom/AuthorPage";
import { NewsroomHomePage } from "./components/newsroom/NewsroomHomePage";

function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [fantasyOpen, setFantasyOpen] = useState(false);
  const [showAllStories, setShowAllStories] = useState(false);
  const [systemStatus, setSystemStatus] = useState<{ season: number; week: number; availableSeasons: number[]; last_updated?: string | null }>({ season: 2035, week: 18, availableSeasons: [2035] });
  const [week, setWeek] = useState(18);
  const [hasOffseasonArticles, setHasOffseasonArticles] = useState(false);
  const [newsData, setNewsData] = useState<any>(null);
  const [powerRankings, setPowerRankings] = useState<PowerRankingsResponse | null>(null);
  const [route, setRoute] = useState(window.location.hash);

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/api/status`).then(r => r.json()),
      fetch(`${API_BASE}/api/seasons`).then(r => r.json())
    ]).then(([statusData, seasonsData]) => {
      setSystemStatus({ ...statusData, availableSeasons: Array.isArray(seasonsData) ? seasonsData : [statusData.season] });
      setWeek(statusData.week);

      fetch(`${API_BASE}/api/power_rankings?season=${statusData.season}`)
        .then(r => r.json())
        .then(rankings => setPowerRankings(rankings))
        .catch(err => console.error("Error fetching power rankings:", err));
    });

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        setStatsOpen(false);
        setSearchOpen(false);
      }
    };
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        if (!window.location.hash.startsWith("#fantasy")) {
          e.preventDefault();
          setSearchOpen((prev) => !prev);
        }
      }
    };
    const updateRoute = () => {
      setRoute(window.location.hash);
      setMenuOpen(false);
      setStatsOpen(false);
      setSearchOpen(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("keydown", handleGlobalKeyDown);
    window.addEventListener("hashchange", updateRoute);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("keydown", handleGlobalKeyDown);
      window.removeEventListener("hashchange", updateRoute);
    };
  }, []);

  useEffect(() => {
    if (!systemStatus.season) return;
    fetch(`${API_BASE}/api/news/week/24?season=${systemStatus.season}`)
      .then(r => r.json())
      .then(data => {
        if (data?.total_articles > 0 || (Array.isArray(data?.articles) && data.articles.length > 0)) {
          setHasOffseasonArticles(true);
        } else {
          setHasOffseasonArticles(false);
        }
      })
      .catch(() => {});
  }, [systemStatus.season, week]);

  useEffect(() => {
    if (!systemStatus.season || !week) return;
    fetch(`${API_BASE}/api/news/week/${week}?season=${systemStatus.season}`)
      .then(r => r.json())
      .then(news => {
        setNewsData(news);
        if (week === 24 && (news?.total_articles > 0 || (Array.isArray(news?.articles) && news.articles.length > 0))) {
          setHasOffseasonArticles(true);
        }
      })
      .catch(err => console.error("Error fetching news:", err));
  }, [week, systemStatus.season]);

  const selectedTeam = route.startsWith("#team-")
    && !route.startsWith("#team-schedule-")
    ? teams.find((team) => team.abbr === route.replace("#team-", ""))
    : undefined;
  const selectedScheduleTeam = route.startsWith("#team-schedule-")
    ? teams.find((team) => team.abbr === route.replace("#team-schedule-", ""))
    : undefined;
  const gameRoute = route.match(/^#game-(\d+)$/);
  const playerRoute = route.match(/^#player-(?:[a-z]+-)?(\d+)$/);
  const authorRoute = route.match(/^#author-(.+)$/);
  const articleRoute = route.match(/^#article-(.+)$/);
  const fantasyArticleRoute = route.match(/^#article-fantasy-(start-sit|p\d+|\d+)$/);
  const socialTeamRoute = route.match(/^#social-team-([a-z]+)$/);
  const socialTeam = socialTeamRoute
    ? teams.find((team) => team.abbr === socialTeamRoute[1])
    : undefined;
  const socialPostRoute = route.match(/^#social-post-(\d+)$/);
  const socialProfileRoute = route.match(/^#social-profile-([A-Za-z0-9_]+)$/);
  const socialTrendRoute = route.match(/^#social-trend-(TEAM|PLAYER|GAME|HASHTAG)-(.+)$/);

  let content: ReactNode = null;
  if (fantasyArticleRoute) {
    content = <FantasyPlayerProvider><FantasyArticlePage slug={fantasyArticleRoute[1]} /></FantasyPlayerProvider>;
  } else if (route === "#fantasy-all") {
    content = <FantasyPlayerProvider><FantasyAllPage /></FantasyPlayerProvider>;
  } else if (route === "#fantasy") {
    content = <FantasyPlayerProvider><FantasyDashboard /></FantasyPlayerProvider>;
  } else if (route === "#fantasy-rankings") {
    content = <FantasyPlayerProvider><FantasyRankingsPage view="weekly" /></FantasyPlayerProvider>;
  } else if (route === "#fantasy-adp") {
    content = <FantasyPlayerProvider><FantasyRankingsPage view="adp" /></FantasyPlayerProvider>;
  } else if (route === "#fantasy-weekly-projections") {
    content = <FantasyPlayerProvider><FantasyRankingsPage view="weekly" /></FantasyPlayerProvider>;
  } else if (route === "#fantasy-ros") {
    content = <FantasyPlayerProvider><FantasyRankingsPage view="ros" /></FantasyPlayerProvider>;
  } else if (route === "#napper" || route.startsWith("#napper-")) {
    const cleanRoute = route.replace("#napper-draft-", "").replace("#napper-draft", "").replace("#napper-", "");
    const leagueId = Number(cleanRoute) || 1;
    content = (
      <NapperPage
        key={route}
        leagueId={leagueId}
        initialLeagueId={leagueId}
        onBackToMu={() => {
          window.location.hash = "";
        }}
      />
    );
  } else if (route === "#draft" || route.startsWith("#draft?")) {
    content = <DraftPage />;
  } else if (route === "#social") {
    content = <SocialFeedPage />;
  } else if (route === "#social-explore") {
    content = <SocialExplorePage />;
  } else if (route === "#social-teams") {
    content = <SocialTeamHubsPage />;
  } else if (socialPostRoute) {
    content = <SocialPostDetailPage postId={Number(socialPostRoute[1])} key={route} />;
  } else if (socialProfileRoute) {
    content = <SocialProfilePage handle={socialProfileRoute[1]} key={route} />;
  } else if (socialTrendRoute) {
    content = <SocialTrendPage kind={socialTrendRoute[1]} trendKey={decodeURIComponent(socialTrendRoute[2])} key={route} />;
  } else if (socialTeam) {
    content = <SocialTeamFeedPage team={socialTeam} key={socialTeam.abbr} />;
  }

  if (content) {
    return (
      <SystemContext.Provider value={systemStatus}>
        {content}
      </SystemContext.Provider>
    );
  }

  return (
    <SystemContext.Provider value={systemStatus}>
      <div className="app-shell">
      <ScoreTicker />
      <header className="site-header">
        <nav className="nav-inner" aria-label="Primary navigation">
          <a className="brand" href="#" aria-label="Gridiron home">
            <span className="brand-mark">G</span>
            <span><b>GRIDIRON</b><small>THE SIMULATION NETWORK</small></span>
          </a>
          <div className="nav-links">
            <button className={`teams-trigger ${menuOpen ? "active" : ""}`} onClick={() => { setMenuOpen(!menuOpen); setStatsOpen(false); }} aria-expanded={menuOpen}>
              Teams <Chevron />
            </button>
            <button className={`stats-trigger ${statsOpen ? "active" : ""}`} onClick={() => { setStatsOpen(!statsOpen); setMenuOpen(false); }} aria-expanded={statsOpen}>
              Stats <Chevron />
            </button>
            <a href="#standings">Standings</a>
            <a href="#schedule">Schedule</a>
            <div style={{ position: "relative", display: "inline-block" }}>
              <button
                className={`fantasy-trigger ${fantasyOpen ? "active" : ""}`}
                onClick={() => { setFantasyOpen(!fantasyOpen); setMenuOpen(false); setStatsOpen(false); }}
                style={{ background: "none", border: "none", font: "inherit", color: "inherit", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
              >
                Fantasy <Chevron />
              </button>
              {fantasyOpen && (
                <div style={{
                  position: "absolute",
                  top: "calc(100% + 8px)",
                  left: "-20px",
                  background: "#ffffff",
                  border: "1px solid var(--gray-300, #cbd5e1)",
                  boxShadow: "0 10px 25px rgba(0,0,0,0.15)",
                  borderRadius: "8px",
                  minWidth: "220px",
                  zIndex: 1000,
                  padding: "6px 0",
                  textAlign: "left"
                }}>
                  <a
                    href="#fantasy"
                    onClick={() => setFantasyOpen(false)}
                    style={{
                      display: "block",
                      padding: "10px 16px",
                      textDecoration: "none",
                      color: "inherit",
                      borderBottom: "1px solid #f1f5f9"
                    }}
                  >
                    <div style={{ fontWeight: "700", fontSize: "14px", color: "#0f172a" }}>📊 Analytics</div>
                    <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>Consensus rankings, ADP &amp; articles</div>
                  </a>
                  <a
                    href="#napper"
                    onClick={() => setFantasyOpen(false)}
                    style={{
                      display: "block",
                      padding: "10px 16px",
                      textDecoration: "none",
                      color: "inherit"
                    }}
                  >
                    <div style={{ fontWeight: "700", fontSize: "14px", color: "#00ceb8" }}>🛏️ Napper</div>
                    <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>Playable leagues, matchups &amp; trades</div>
                  </a>
                </div>
              )}
            </div>
            <a href="#social">Social</a>
          </div>
          <div className="nav-tools">
            <button
              aria-label="Search"
              onClick={() => setSearchOpen(true)}
              title="Search players or teams (⌘K)"
            >
              <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
            </button>
            <a className="live-dot" href="#debug" title="Debug: league state, tables, runs">LIVE</a>
          </div>
        </nav>
        {menuOpen && <MegaMenu week={week} setWeek={setWeek} close={() => setMenuOpen(false)} />}
        {statsOpen && <StatsDropdown close={() => setStatsOpen(false)} />}
        {searchOpen && <GlobalSearchModal close={() => setSearchOpen(false)} />}
      </header>

      {route === "#debug" ? (
        <DebugPage />
      ) : authorRoute ? (
        <AuthorPage authorKey={authorRoute[1]} key={route} />
      ) : articleRoute ? (
        <ArticlePage articleId={articleRoute[1]} key={route} />
      ) : playerRoute ? (
        <PlayerPage category="any" playerId={Number(playerRoute[1])} key={route} />
      ) : gameRoute ? (
        <GamePage gameId={Number(gameRoute[1])} key={route} />
      ) : route === "#stats-team" ? (
        <StatsPage mode="team" />
      ) : route === "#stats-player" ? (
        <StatsPage mode="player" />
      ) : route === "#records" || route.startsWith("#records?") ? (
        <RecordBookPage query={route.slice("#records".length)} teams={teams} />
      ) : route === "#awards" || route.startsWith("#awards?") ? (
        <AwardsPage query={route.slice("#awards".length)} teams={teams} />
      ) : route === "#standings" ? (
        <StandingsPage />
      ) : route === "#rankings" ? (
        <PowerRankingsPage />
      ) : selectedScheduleTeam ? (
        <TeamSchedulePage team={selectedScheduleTeam} key={selectedScheduleTeam.abbr} />
      ) : selectedTeam ? (
        <TeamPage team={selectedTeam} key={selectedTeam.abbr} />
      ) : route === "#schedule" ? (
        <SchedulePage />
      ) : (
        <NewsroomHomePage
          week={week}
          setWeek={setWeek}
          newsData={newsData}
          hasOffseasonArticles={hasOffseasonArticles}
          powerRankings={powerRankings}
          showAllStories={showAllStories}
          setShowAllStories={setShowAllStories}
        />
      )}
      <footer>
        <div className="footer-brand">GRIDIRON<span>25</span></div>
        <p>A fictional football simulation network. All scores and reporting are simulated.</p>
        <div><a href="#about">About</a><a href="#privacy">Privacy</a><a href="#terms">Terms</a></div>
      </footer>
    </div>
    </SystemContext.Provider>
  );
}

export default App;
