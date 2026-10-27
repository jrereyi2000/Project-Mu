import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getDivision, getTeamPosition, logo, playerNames, type Team, teams } from "../../lib/gridiron";
import FranchiseRecords from "../history/FranchiseRecords";
import FrontOfficeTab, { type ContractFocus } from "./FrontOfficeTab";
import TeamHistory from "./TeamHistory";

export function TeamPage({ team }: { team: Team }) {
  const systemStatus = useContext(SystemContext);
  const [tab, setTab] = useState<"main" | "roster" | "stats" | "history" | "front office">("main");
  // A Roster row's "Contract" link opens the Front Office tab on that player's contract row.
  const [contractFocus, setContractFocus] = useState<ContractFocus | null>(null);
  const [teamData, setTeamData] = useState<any>(null);
  const [standings, setStandings] = useState<any[]>([]);
  const [schedule, setSchedule] = useState<any[]>([]);
  const [seasonStats, setSeasonStats] = useState<any>(null);
  const [teamNews, setTeamNews] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/team/${team.abbr}?season=${systemStatus.season}`)
      .then(res => res.json())
      .then(data => {
        setTeamData(data);
        if (data && !data.error && data.team) {
          fetch(`${API_BASE}/api/team/${data.team.id}/season_stats/${systemStatus.season}`)
            .then(res => res.json())
            .then(stats => setSeasonStats(stats));
        }
      });
      
    fetch(`${API_BASE}/api/standings/${systemStatus.season}`)
      .then((res) => res.json())
      .then((data) => setStandings(data));

    fetch(`${API_BASE}/api/team/${team.abbr}/schedule?season=${systemStatus.season}`)
      .then((res) => res.json())
      .then((data) => {
        if (!data.error) setSchedule(data);
      });

    fetch(`${API_BASE}/api/team/${team.abbr}/news?season=${systemStatus.season}`)
      .then((res) => res.json())
      .then((data) => {
        if (data && Array.isArray(data.articles)) {
          setTeamNews(data.articles);
        }
      })
      .catch((err) => console.error("Error fetching team news:", err));
  }, [team.abbr, systemStatus.season]);

  const teamIndex = teams.findIndex((item) => item.abbr === team.abbr);
  const division = getDivision(team.abbr);
  
  let ordinal = "1st";
  if (standings.length > 0) {
    const divisionTeams = standings
      .filter((s) => s.division === division)
      .sort((a, b) => b.wins - a.wins || (b.pointsFor - b.pointsAgainst) - (a.pointsFor - a.pointsAgainst));
    const rankIndex = divisionTeams.findIndex((s) => s.abbr.toLowerCase() === team.abbr.toLowerCase());
    if (rankIndex !== -1) {
      ordinal = ["1st", "2nd", "3rd", "4th"][rankIndex];
    }
  } else {
    const position = getTeamPosition(team.abbr);
    ordinal = ["1st", "2nd", "3rd", "4th"][position - 1];
  }

  const nickname = team.name.split(" ").at(-1);

  const record = teamData && !teamData.error ? `${teamData.team.wins}-${teamData.team.losses}` : "0-0";
  const roster = teamData && !teamData.error ? teamData.roster : [];

  const teamArticles = [
    {
      label: "Film Study",
      title: `The adjustment that could unlock the ${nickname}' postseason run`,
      excerpt: `A closer look at the personnel package changing how opponents prepare for ${team.name}.`,
    },
    {
      label: "Locker Room",
      title: `${playerNames[teamIndex % playerNames.length]} is setting the standard when it matters most`,
      excerpt: "Teammates explain the preparation, patience and leadership driving a late-season surge.",
    },
    {
      label: "Matchup Preview",
      title: `Three keys for ${team.name} heading into Week 17`,
      excerpt: "The pressure points, coverage tendencies and hidden matchups that will decide Sunday.",
    },
    {
      label: "Roster Report",
      title: `Why the young core is ahead of schedule`,
      excerpt: "Development at three premium positions has changed the franchise's competitive timeline.",
    },
  ];

  return (
    <main className="team-page" style={{ "--team-color": teamData?.team?.team_color || "#e31837" } as React.CSSProperties}>
      <section className="team-masthead">
        <div className="team-masthead-bg" />
        <div className="team-identity">
          <img src={logo(team.abbr)} alt={`${team.name} logo`} />
          <div>
            <span>{division}</span>
            <h1>{team.name}</h1>
            <a href="#standings" className="team-standing" style={{ textDecoration: 'none', color: 'inherit' }}>
              <strong>{record}</strong>
              <i />
              <b>{ordinal} in {division}</b>
            </a>
          </div>
        </div>
      </section>

      <nav className="team-tabs" aria-label={`${team.name} sections`}>
        {(["main", "roster", "stats", "history", "front office"] as const).map((item) => (
          <button className={tab === item ? "active" : ""} onClick={() => { setTab(item); setContractFocus(null); }} key={item}>
            {item}
          </button>
        ))}
        <a href={`#team-schedule-${team.abbr}`}>Schedule</a>
      </nav>

      {tab === "main" && (
        <div className="team-content-grid">
          <section className="team-news">
            <div className="team-section-title"><span>Team Coverage</span><h2>Latest from the beat</h2></div>
            {(() => {
              const heroArticle = teamNews.length > 0 ? teamNews[0] : null;
              const heroTitle = heroArticle?.headline || `Inside the belief driving ${team.name} into the stretch run`;
              const heroExcerpt = heroArticle?.excerpt || `One locker room, one shared standard and a season still waiting to be defined. Our beat reporter goes inside the week that changed everything.`;
              const heroAuthor = heroArticle?.author?.display_name || "Jordan Ellis";
              const heroRole = heroArticle ? "FRANCHISE BEAT" : `${team.name.toUpperCase()} BEAT REPORTER`;
              const heroLink = heroArticle ? `#article-${heroArticle.id}` : `#article-team-hero-${team.abbr}`;
              const heroSlot = heroArticle?.slot || "Exclusive";

              return (
                <article className="team-hero">
                  <a className="team-hero-image" href={heroLink}>
                    <img
                      src="https://images.unsplash.com/photo-1704713611761-e0fbbdecc3e9?auto=format&fit=crop&w=1500&q=85"
                      alt="Football players running onto the field"
                    />
                    <span>{team.name} Spotlight</span>
                  </a>
                  <div className="team-hero-copy">
                    <span>{heroSlot} · 9 min read</span>
                    <a href={heroLink}><h2>{heroTitle}</h2></a>
                    <p>{heroExcerpt}</p>
                    <div className="author-row">
                      <span className="avatar">{team.abbr.toUpperCase()}</span>
                      <span><b>{heroAuthor}</b><small>{heroRole}</small></span>
                    </div>
                  </div>
                </article>
              );
            })()}

            <div className="team-article-feed">
              {(() => {
                // If we have live DB articles beyond the hero (slice 1), prioritize them; fill remainder with templates
                const dbFeed = teamNews.slice(1);
                const feedItems: any[] = [];
                
                dbFeed.forEach((art) => {
                  feedItems.push({
                    isDb: true,
                    id: art.id,
                    week: art.week,
                    season: art.season,
                    label: art.article_type ? art.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "Beat Report",
                    title: art.headline,
                    excerpt: art.excerpt,
                    author: art.author?.display_name || "Beat Reporter",
                    authorHandle: art.author?.handle,
                    link: `#article-${art.id}`
                  });
                });

                if (feedItems.length === 0) {
                  teamArticles.forEach((tmpl, idx) => {
                    feedItems.push({
                      isDb: false,
                      id: null,
                      week: systemStatus.week,
                      season: systemStatus.season,
                      label: tmpl.label,
                      title: tmpl.title,
                      excerpt: tmpl.excerpt,
                      author: "Jordan Ellis",
                      authorHandle: null,
                      link: `#article-team-${team.abbr}-${idx}`
                    });
                  });
                }

                return feedItems.map((article, index) => (
                  <article className="team-feed-card" key={article.id || `${article.title}-${index}`}>
                    <div className="team-feed-card-top">
                      <span className="story-number">{index + 1 < 10 ? `0${index + 1}` : index + 1}</span>
                      <span className="kicker">{article.week ? `WK ${article.week} · ` : ""}{article.label}</span>
                    </div>
                    <div className="team-feed-card-body">
                      <a href={article.link}><h3>{article.title}</h3></a>
                      <p>{article.excerpt}</p>
                    </div>
                    <div className="team-feed-card-footer">
                      <small>
                        By {article.authorHandle ? (
                          <a href={`#author-${article.authorHandle.replace(/^@/, '')}`} className="author-link">{article.author}</a>
                        ) : article.author}
                      </small>
                    </div>
                  </article>
                ));
              })()}
            </div>
          </section>

          <aside className="team-schedule">
            <div className="team-section-title"><span>Week {systemStatus.week}</span><h2>Schedule</h2></div>
            <div className="schedule-summary">
              {schedule
                .filter((g) => g.week >= systemStatus.week)
                .slice(0, 3)
                .map((game) => {
                  const isHome = game.home_abbr.toLowerCase() === team.abbr.toLowerCase();
                  const opponentAbbr = isHome ? game.away_abbr.toLowerCase() : game.home_abbr.toLowerCase();
                  const opponentName = isHome ? (game.away_name || game.away_team) : (game.home_name || game.home_team);
                  const isPast = game.status === "COMPLETED";
                  const fullTime = game.game_time ? game.game_time.toUpperCase() : "TBD";
                  const teamScore = isHome ? game.home_score : game.away_score;
                  const opponentScore = isHome ? game.away_score : game.home_score;
                  const isWin = teamScore > opponentScore;
                  const isTie = teamScore === opponentScore;
                  const resultStr = isTie ? "T" : isWin ? "W" : "L";
                  
                  return (
                    <a
                      className={isPast ? "completed" : ""}
                      href={`#game-${game.id}`}
                      key={game.week}
                    >
                      <div className="schedule-week">
                        <span>WEEK {game.week}</span>
                        <b>{isPast ? "FINAL" : fullTime}</b>
                      </div>
                      <div className="schedule-opponent">
                        <span>{isHome ? "VS" : "AT"}</span>
                        <img src={logo(opponentAbbr)} alt="" />
                        <div><strong>{opponentName}</strong><small>{getDivision(opponentAbbr)}</small></div>
                        {isPast && <em>{resultStr} {Math.max(teamScore, opponentScore)}–{Math.min(teamScore, opponentScore)}</em>}
                      </div>
                    </a>
                  );
                })}
            </div>
            <a href={`#team-schedule-${team.abbr}`}>View full team schedule <span>→</span></a>
          </aside>
        </div>
      )}

      {tab === "roster" && (
        <section className="team-data-view">
          <div className="team-section-title"><span>Active Players</span><h2>{team.name} Roster</h2></div>
          <div className="roster-table with-contract">
            <div className="data-row data-head"><span>Player</span><span>Pos</span><span>No.</span><span>Experience</span><span>Contract</span></div>
            {roster.map((player: any) => (
              <div className="data-row" key={player.id}>
                <span>
                  <a href={`#player-${player.id}`}><b>{player.name}</b></a>
                  <small>{team.name}</small>
                </span>
                <span>{player.position}</span>
                <span>{player.player_number}</span>
                <span>{player.years_of_experience == null ? "—"
                  : player.years_of_experience === 0 ? "Rookie"
                  : `${player.years_of_experience} yr${player.years_of_experience === 1 ? "" : "s"}`}</span>
                <span>
                  <button className="roster-contract-link" aria-label={`${player.name}'s contract`}
                    onClick={() => { setContractFocus({ playerId: player.id, name: player.name, nonce: Date.now() }); setTab("front office"); }}>
                    View →
                  </button>
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === "stats" && (
        <section className="team-data-view">
            <div className="team-section-title"><span>Season {systemStatus.season}</span><h2>Team Statistics</h2></div>
            <div className="stats-grid">
              {[
                ["Points Per Game", seasonStats ? seasonStats.points_per_game : "-", "PPG"],
                ["Points Allowed Per Game", seasonStats ? seasonStats.points_allowed_per_game : "-", "PAPG"],
                ["Total Yards", seasonStats ? seasonStats.total_yards : "-", "YDS"],
                ["Pass Yards / Game", seasonStats ? seasonStats.pass_yards_per_game : "-", "PYPG"],
                ["Rush Yards / Game", seasonStats ? seasonStats.rush_yards_per_game : "-", "RYPG"],
              ].map((stat) => (
                <article key={stat[0]}><span>{stat[0]}</span><strong>{stat[1]}</strong><small>SEASON STAT · {stat[2]}</small></article>
              ))}
            </div>

          <div style={{ height: "4rem" }}></div>
          <div className="team-section-title"><span>Season {systemStatus.season}</span><h2>Team Ratings</h2></div>
            <div className="stats-grid">
              {[
                ["Overall Rating", teamData ? teamData.team.team_ovr : "-", "OVR"],
                ["Offense Rating", teamData ? teamData.team.off_ovr : "-", "OFF"],
                ["Defense Rating", teamData ? teamData.team.def_ovr : "-", "DEF"],
                ["Total Wins", teamData ? teamData.team.wins : "-", "W"],
                ["Total Losses", teamData ? teamData.team.losses : "-", "L"],
              ].map((stat) => (
                <article key={stat[0]}><span>{stat[0]}</span><strong>{stat[1]}</strong><small>TEAM RATING · {stat[2]}</small></article>
              ))}
            </div>

          <div style={{ height: "4rem" }}></div>
          <div className="team-section-title"><span>Set in this league</span><h2>Records &amp; Titles</h2></div>
          <FranchiseRecords abbr={team.abbr} compact />
          </section>
      )}

      {tab === "history" && <TeamHistory abbr={team.abbr} />}
      {tab === "front office" && <FrontOfficeTab abbr={team.abbr} season={systemStatus.season} focus={contractFocus} />}
    </main>
  );
}
