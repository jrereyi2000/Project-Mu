import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { API_BASE } from "./api";
import DebugPage from "./DebugPage";
import { DraftPage } from "./DraftPage";
import { NapperPage } from "./NapperPage";
import FrontOfficeTab, { type ContractFocus } from "./components/team/FrontOfficeTab";
import TeamHistory from "./components/team/TeamHistory";
import AwardsPage from "./components/history/AwardsPage";
import FranchiseRecords from "./components/history/FranchiseRecords";
import PlayerHonors from "./components/history/PlayerHonors";
import RecordBookPage from "./components/history/RecordBookPage";
export const SystemContext = createContext<{ season: number; week: number; availableSeasons: number[]; last_updated?: string | null }>({ season: 2035, week: 18, availableSeasons: [2035] });

/** "Oct 15 · 8:03 AM" for the Stats page's Updated stamp (the latest capture load). */
function formatUpdated(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}

type Team = { name: string; abbr: string };

const teams: Team[] = [
  { name: "Arizona Cardinals", abbr: "ari" },
  { name: "Atlanta Falcons", abbr: "atl" },
  { name: "Baltimore Ravens", abbr: "bal" },
  { name: "Buffalo Bills", abbr: "buf" },
  { name: "Carolina Panthers", abbr: "car" },
  { name: "Chicago Bears", abbr: "chi" },
  { name: "Cincinnati Bengals", abbr: "cin" },
  { name: "Cleveland Browns", abbr: "cle" },
  { name: "Dallas Cowboys", abbr: "dal" },
  { name: "Denver Broncos", abbr: "den" },
  { name: "Detroit Lions", abbr: "det" },
  { name: "Green Bay Packers", abbr: "gb" },
  { name: "Houston Texans", abbr: "hou" },
  { name: "Indianapolis Colts", abbr: "ind" },
  { name: "Jacksonville Jaguars", abbr: "jax" },
  { name: "Kansas City Chiefs", abbr: "kc" },
  { name: "Las Vegas Raiders", abbr: "lv" },
  { name: "Los Angeles Chargers", abbr: "lac" },
  { name: "Los Angeles Rams", abbr: "lar" },
  { name: "Miami Dolphins", abbr: "mia" },
  { name: "Minnesota Vikings", abbr: "min" },
  { name: "New England Patriots", abbr: "ne" },
  { name: "New Orleans Saints", abbr: "no" },
  { name: "New York Giants", abbr: "nyg" },
  { name: "New York Jets", abbr: "nyj" },
  { name: "Philadelphia Eagles", abbr: "phi" },
  { name: "Pittsburgh Steelers", abbr: "pit" },
  { name: "San Francisco 49ers", abbr: "sf" },
  { name: "Seattle Seahawks", abbr: "sea" },
  { name: "Tampa Bay Buccaneers", abbr: "tb" },
  { name: "Tennessee Titans", abbr: "ten" },
  { name: "Washington Commanders", abbr: "wsh" },
];

const logo = (abbr: string) => {
  const clean = (abbr || "").toLowerCase();
  const normalized = clean === "az" ? "ari" : clean;
  return `https://a.espncdn.com/i/teamlogos/nfl/500/${normalized}.png`;
};

const DEFAULT_AVATAR = "https://sleepercdn.com/images/v2/icons/player_default.webp";

// Bump whenever static/players/ is regenerated so browsers can't reuse previously cached headshots.
const HEADSHOT_VERSION = "2026-10-12-crop";

type HeadshotPlayer ={ espn_id?: number | string | null; id?: number | string | null } | null | undefined;

// Ordered headshot sources: ESPN (NFL, then college for players still in school), then the generated
// headshot served by api.py from static/players/{player_id}.webp, then the placeholder.
const getHeadshotCandidates = (player: HeadshotPlayer) => {
  const urls: string[] = [];
  if (player?.espn_id) {
    urls.push(`https://a.espncdn.com/i/headshots/nfl/players/full/${player.espn_id}.png`);
    urls.push(`https://a.espncdn.com/i/headshots/college-football/players/full/${player.espn_id}.png`);
  }
  if (player?.id != null && /^\d+$/.test(String(player.id))) {
    urls.push(`${API_BASE}/static/players/${player.id}.webp?v=${HEADSHOT_VERSION}`);
  }
  urls.push(DEFAULT_AVATAR);
  return urls;
};

const getPlayerHeadshot = (player: HeadshotPlayer) => getHeadshotCandidates(player)[0];

// <img onError> handler: advance to the next headshot source each time one fails to load.
const handleHeadshotError = (e: React.SyntheticEvent<HTMLImageElement>, player: HeadshotPlayer) => {
  const img = e.currentTarget;
  const candidates = getHeadshotCandidates(player);
  const key = candidates.join("|");
  const attempt = img.dataset.headshotKey === key ? Number(img.dataset.headshotAttempt || 0) + 1 : 1;
  img.dataset.headshotKey = key;
  img.dataset.headshotAttempt = String(attempt);
  if (attempt < candidates.length) img.src = candidates[attempt];
  return attempt < candidates.length ? candidates[attempt] : null;
};

const tickerGames = [
  ["4:25 PM", "KC", "kc", "27", "BUF", "buf", "24"],
  ["8:20 PM", "SF", "sf", "31", "SEA", "sea", "17"],
  ["4:25 PM", "DAL", "dal", "20", "PHI", "phi", "23"],
  ["1:00 PM", "BAL", "bal", "34", "CIN", "cin", "21"],
  ["8:15 PM", "DET", "det", "38", "GB", "gb", "24"],
];

const stories = [
  {
    kicker: "Film Room",
    title: "The pressure package reshaping the AFC playoff picture",
    blurb:
      "Three disguised looks, one impossible choice. How simulated defenses are forcing elite quarterbacks off their first read.",
    author: "Mina Kimes",
    initials: "MK",
    read: "8 min read",
  },
  {
    kicker: "League Intel",
    title: "Five teams that can still crash the postseason party",
    blurb:
      "The models see chaos ahead. We break down the contenders with the schedules—and the rosters—to make a late run.",
    author: "Seth Walder",
    initials: "SW",
    read: "6 min read",
  },
  {
    kicker: "Draft Lab",
    title: "The next generation: Scouting the class of tomorrow",
    blurb:
      "Speed at the edges, size inside and a quarterback class loaded with variance. Our early board is here.",
    author: "Matt Miller",
    initials: "MM",
    read: "11 min read",
  },
  {
    kicker: "Quarterback Room",
    title: "The four throws defining this year's MVP race",
    blurb: "A film-based comparison of the anticipation, arm talent and late-game decisions separating the finalists.",
    author: "Dan Orlovsky",
    initials: "DO",
    read: "7 min read",
  },
  {
    kicker: "Numbers Game",
    title: "What every contender must fix before the postseason",
    blurb: "One revealing weakness for each playoff-bound roster—and whether there is still time to solve it.",
    author: "Aaron Schatz",
    initials: "AS",
    read: "9 min read",
  },
  {
    kicker: "Power Rankings",
    title: "A new team takes the top spot with two weeks left",
    blurb: "The league's balance of power shifts again after a statement win under the brightest lights.",
    author: "Eric Moody",
    initials: "EM",
    read: "5 min read",
  },
  {
    kicker: "Scheme Lab",
    title: "Why motion is becoming the league's most valuable weapon",
    blurb: "The best offenses are creating answers before the snap. These five concepts explain how.",
    author: "Ben Solak",
    initials: "BS",
    read: "10 min read",
  },
  {
    kicker: "Playoff Machine",
    title: "Every path to the postseason entering Week 17",
    blurb: "Clinching scenarios, elimination games and the tiebreakers that could decide the final seven spots.",
    author: "Kevin Seifert",
    initials: "KS",
    read: "8 min read",
  },
];

const localStories = [
  {
    team: "dal",
    tag: "Dallas Cowboys Beat",
    title: "Why Dallas is finally letting its secondary play fast",
    author: "Todd Archer",
    time: "18m",
  },
  {
    team: "phi",
    tag: "Philadelphia Eagles Beat",
    title: "The subtle adjustment unlocking Philly's run game",
    author: "Tim McManus",
    time: "42m",
  },
  {
    team: "buf",
    tag: "Buffalo Bills Beat",
    title: "Inside Buffalo's fourth-quarter identity shift",
    author: "Alaina Getzenberg",
    time: "1h",
  },
  {
    team: "sf",
    tag: "San Francisco 49ers Beat",
    title: "A new chess piece is emerging in the Bay",
    author: "Nick Wagoner",
    time: "2h",
  },
  {
    team: "det",
    tag: "Detroit Lions Beat",
    title: "The North runs through Detroit—and the numbers agree",
    author: "Eric Woodyard",
    time: "3h",
  },
];

const divisions: Record<string, string[]> = {
  "AFC East": ["buf", "mia", "ne", "nyj"],
  "AFC North": ["bal", "cin", "cle", "pit"],
  "AFC South": ["hou", "ind", "jax", "ten"],
  "AFC West": ["den", "kc", "lv", "lac"],
  "NFC East": ["dal", "nyg", "phi", "wsh"],
  "NFC North": ["chi", "det", "gb", "min"],
  "NFC South": ["atl", "car", "no", "tb"],
  "NFC West": ["ari", "lar", "sf", "sea"],
};

const playerNames = [
  "Marcus Reed", "Devin Carter", "Jalen Brooks", "Tyson Mitchell", "Andre Hayes",
  "Caleb Foster", "Malik Robinson", "Darius Walker", "Trey Coleman", "Nolan Price",
  "Isaiah Bennett", "Cameron Ward",
];

function getPlayerProfile(category: string, index: number) {
  const team = teams[(index * 5 + 2) % teams.length];
  const name = `${playerNames[index % playerNames.length]}${index >= playerNames.length ? ` ${index % 2 ? "Jr." : "II"}` : ""}`;
  const position = category === "passing" ? "QB" : category === "rushing" ? "HB" : category === "receiving" ? "WR" : category === "tightend" ? "TE" : category === "fullback" ? "FB" : ["QB", "HB", "WR", "TE", "FB"][index % 5];
  return { name, team, position, number: 2 + (index % 88) };
}

function getPlayerIdForTeam(team: Team, slot: number) {
  const baseId = ((teams.indexOf(team) - 2) * 13 + 32) % 32;
  return baseId + slot * 32;
}

function getDivision(abbr: string) {
  return Object.entries(divisions).find(([, members]) => members.includes(abbr))?.[0] ?? "NFL";
}

function getTeamPosition(abbr: string) {
  const division = getDivision(abbr);
  const members = divisions[division];
  return members.indexOf(abbr) + 1;
}

function TeamPage({ team }: { team: Team }) {
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
                .map((game, index) => {
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

function SchedulePage() {
  const systemStatus = useContext(SystemContext);
  const [selectedWeek, setSelectedWeek] = useState(systemStatus.week);
  const [selectedSeason, setSelectedSeason] = useState(systemStatus.season);
  
  // Re-sync if context updates before we mount properly
  useEffect(() => {
    if (systemStatus.week !== selectedWeek) setSelectedWeek(systemStatus.week);
    if (systemStatus.season !== selectedSeason) setSelectedSeason(systemStatus.season);
  }, [systemStatus]);
  
  const [seasonOpen, setSeasonOpen] = useState(false);
  const [matchups, setMatchups] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/schedule?season=${selectedSeason}&week=${selectedWeek}`)
      .then(res => res.json())
      .then(data => {
        // Map the API data back to the format the UI expects
        const formatted = data.map((game: any) => {
          const parts = game.game_time ? game.game_time.split(" ") : ["Sun", "TBD"];
          const dayMap: Record<string, string> = { "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday", "Mon": "Monday" };
          const dayName = parts[0] ? (dayMap[parts[0]] || parts[0]) : "Sunday";
          const time = parts.length > 1 ? parts.slice(1).join(" ").toUpperCase() : "TBD";
          
          let network = "GRIDIRON+";
          if (dayName === "Thursday") network = "PRIME";
          else if (dayName === "Monday") network = "ESPN";
          else if (dayName === "Sunday") {
            if (time.includes("8:") || time.includes("7:")) network = "NBC / PEACOCK";
            else network = game.id % 2 === 0 ? "CBS" : "FOX";
          }
          
          return {
            id: game.id,
            away: { name: game.away_name, abbr: game.away_abbr },
            home: { name: game.home_name, abbr: game.home_abbr },
            awayRecord: `${game.away_wins}-${game.away_losses}`,
            homeRecord: `${game.home_wins}-${game.home_losses}`,
            awayScore: game.away_score,
            homeScore: game.home_score,
            isFinal: game.status === "COMPLETED",
            day: dayName,
            time: time,
            network: network,
          };
        });
        setMatchups(formatted);
      });
  }, [selectedSeason, selectedWeek]);

  const groups = Array.from(new Set(matchups.map(g => g.day)));

  return (
    <main className="schedule-page">
      <section className="schedule-masthead">
        <div>
          <div className="schedule-title-row">
            <div>
              <span>{selectedSeason} REGULAR SEASON</span>
              <h1>League Schedule</h1>
              <p>Every matchup. Every market. One week at a time.</p>
            </div>
            <div className="season-select" onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setSeasonOpen(false);
            }}>
              <span>Season</span>
              <button className={seasonOpen ? "open" : ""} onClick={() => setSeasonOpen(!seasonOpen)} aria-expanded={seasonOpen}>
                <span><strong>{selectedSeason}</strong>{selectedSeason === systemStatus.season ? "Current season" : "Past season"}</span>
                <Chevron />
              </button>
              {seasonOpen && (
                <div className="season-menu">
                  <div className="season-menu-label">Select season</div>
                  {systemStatus.availableSeasons.map((season) => (
                    <button
                      className={selectedSeason === season ? "selected" : ""}
                      onClick={() => {
                        setSelectedSeason(season);
                        setSeasonOpen(false);
                      }}
                      key={season}
                    >
                      <span>{season}</span>
                      <small>{season === systemStatus.season ? "Current" : "Full season"}</small>
                      {selectedSeason === season && <i>✓</i>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="week-selector" aria-label="Select schedule week">
        <button
          onClick={() => setSelectedWeek(Math.max(1, selectedWeek - 1))}
          disabled={selectedWeek === 1}
          aria-label="Previous week"
        >
          <Chevron direction="left" />
          <span><small>Previous</small>Week {Math.max(1, selectedWeek - 1)}</span>
        </button>
        <div>
          <span>{selectedSeason} SEASON</span>
          <strong>Week {selectedWeek}</strong>
          <small>{selectedWeek === 17 ? "DEC 24–28" : selectedWeek < 17 ? "PREVIOUS WEEK" : "UPCOMING WEEK"}</small>
        </div>
        <button
          onClick={() => setSelectedWeek(Math.min(18, selectedWeek + 1))}
          disabled={selectedWeek === 18}
          aria-label="Next week"
        >
          <span><small>Next</small>Week {Math.min(18, selectedWeek + 1)}</span>
          <Chevron direction="right" />
        </button>
      </section>

      <div className="schedule-list-wrap">
        <div className="schedule-list-heading">
          <div><span className="red-rule" /><h2>Week {selectedWeek} Matchups</h2></div>
          <span>ALL TIMES EASTERN</span>
        </div>
        <div className="weekly-games">
          {groups.map((day) => (
            <section className="game-day" key={day}>
              <div className="game-day-label"><span>{day}</span><i /></div>
              {matchups.filter((game) => game.day === day).map((game, index) => {
                const isFinal = game.isFinal;
                return (
                  <a className="league-game" href={`#game-${game.id}`} key={game.id}>
                    <div className="game-broadcast">
                      <span>{isFinal ? `FINAL · ${game.time}` : game.time}</span>
                      <small>{game.network}</small>
                    </div>
                    <div className="league-matchup">
                      <div className="schedule-team away-team">
                        <span><strong>{game.away.name}</strong><small>{game.awayRecord} · AWAY</small></span>
                        <img src={logo(game.away.abbr.toLowerCase())} alt={`${game.away.name} logo`} />
                        {isFinal && <b>{game.awayScore}</b>}
                      </div>
                      <span className="versus">{isFinal ? "—" : "@"}</span>
                      <div className="schedule-team">
                        <img src={logo(game.home.abbr.toLowerCase())} alt={`${game.home.name} logo`} />
                        <span><strong>{game.home.name}</strong><small>{game.homeRecord} · HOME</small></span>
                        {isFinal && <b>{game.homeScore}</b>}
                      </div>
                    </div>
                    <span className="game-details">Matchup details <span>→</span></span>
                  </a>
                );
              })}
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}

function TeamSchedulePage({ team }: { team: Team }) {
  const systemStatus = useContext(SystemContext);
  const [season, setSeason] = useState(systemStatus.season);

  // Re-sync if context updates
  useEffect(() => {
    if (systemStatus.season !== season) setSeason(systemStatus.season);
  }, [systemStatus.season]);

  const [seasonOpen, setSeasonOpen] = useState(false);
  const [games, setGames] = useState<any[]>([]);
  const [teamData, setTeamData] = useState<any>(null);

  useEffect(() => {
    // Fetch schedule
    fetch(`${API_BASE}/api/team/${team.abbr}/schedule?season=${season}`)
      .then(res => res.json())
      .then(data => {
        if (!data.error) {
          const formatted = data.map((game: any) => {
            const isHome = game.home_abbr.toLowerCase() === team.abbr.toLowerCase();
            const opponentName = isHome ? game.away_name : game.home_name;
            const opponentAbbr = isHome ? game.away_abbr : game.home_abbr;
            const teamScore = isHome ? game.home_score : game.away_score;
            const opponentScore = isHome ? game.away_score : game.home_score;
            const isFinal = game.status === "COMPLETED";
            const parts = game.game_time ? game.game_time.split(" ") : [];
            const time = parts.length > 1 ? parts.slice(1).join(" ").toUpperCase() : "TBD";
            const fullTime = game.game_time ? game.game_time.toUpperCase() : "TBD";
            
            return {
              id: game.id,
              week: game.week,
              time: fullTime,
              opponent: { name: opponentName, abbr: opponentAbbr },
              home: isHome,
              final: isFinal,
              bye: false, // We'll assume no bye week for now unless missing weeks
              teamScore,
              opponentScore,
              isWin: teamScore > opponentScore,
              isTie: teamScore === opponentScore
            };
          });
          setGames(formatted);
        }
      });
      
    // Fetch team info for record
    fetch(`${API_BASE}/api/team/${team.abbr}?season=${season}`)
      .then(res => res.json())
      .then(data => setTeamData(data));
  }, [team.abbr, season]);

  const teamRecord = teamData && !teamData.error ? `${teamData.team.wins}-${teamData.team.losses}` : "0-0";
  const divisionPosition = getTeamPosition(team.abbr);
  const divisionOrdinal = ["1st", "2nd", "3rd", "4th"][divisionPosition - 1];

  return (
    <main className="team-schedule-page" style={{ "--team-color": teamData?.team?.team_color || "#e31837" } as React.CSSProperties}>
      <section className="team-schedule-masthead">
        <div className="team-schedule-identity">
          <img src={logo(team.abbr)} alt={`${team.name} logo`} />
          <div>
            <a href={`#team-${team.abbr}`}>← Team home</a>
            <span>{season} REGULAR SEASON</span>
            <h1>{team.name}<br /><em>Schedule</em></h1>
            <p>{teamRecord} · {divisionOrdinal} in {getDivision(team.abbr)}</p>
          </div>
          <div className="season-select team-season-select" onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setSeasonOpen(false);
          }}>
            <span>Season</span>
            <button className={seasonOpen ? "open" : ""} onClick={() => setSeasonOpen(!seasonOpen)} aria-expanded={seasonOpen}>
              <span><strong>{season}</strong>{season === systemStatus.season ? "Current season" : "Past season"}</span>
              <Chevron />
            </button>
            {seasonOpen && (
              <div className="season-menu">
                <div className="season-menu-label">Select season</div>
                {systemStatus.availableSeasons.map((item) => (
                  <button
                    className={season === item ? "selected" : ""}
                    onClick={() => { setSeason(item); setSeasonOpen(false); }}
                    key={item}
                  >
                    <span>{item}</span>
                    <small>{item === systemStatus.season ? "Current" : "Full season"}</small>
                    {season === item && <i>✓</i>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <div className="team-full-schedule">
        <div className="schedule-list-heading">
          <div><span className="red-rule" /><h2>{season} Game Log</h2></div>
          <span>{team.name.toUpperCase()} · ALL TIMES EASTERN</span>
        </div>
        <div className="team-game-list">
          {games.map((game) => {
            if (game.bye) {
              return (
                <article className="team-week-row bye-week" key={game.week}>
                  <span>WEEK {game.week}</span>
                  <div><strong>Bye Week</strong><small>Rest and recovery</small></div>
                </article>
              );
            }
            return (
              <a className={`team-week-row`} href={`#game-${game.id}`} key={game.week}>
                <div className="team-week-meta">
                  <span>WEEK {game.week}</span>
                  <small>{game.final ? `FINAL · ${game.time}` : game.time}</small>
                </div>
                <div className="team-week-opponent">
                  <span>{game.home ? "VS" : "AT"}</span>
                  <img src={logo(game.opponent.abbr.toLowerCase())} alt="" />
                  <div><strong>{game.opponent.name}</strong><small>{getDivision(game.opponent.abbr)}</small></div>
                </div>
                <div className="team-week-result">
                  {game.final ? (
                    <>
                      <b className={game.isTie ? "tie" : game.isWin ? "win" : "loss"}>{game.isTie ? "T" : game.isWin ? "W" : "L"}</b>
                      <strong>{Math.max(game.teamScore, game.opponentScore)}–{Math.min(game.teamScore, game.opponentScore)}</strong>
                    </>
                  ) : <span>UPCOMING</span>}
                </div>
                <span className="opponent-page">Game details →</span>
              </a>
            );
          })}
        </div>
      </div>
    </main>
  );
}

function GamePage({ gameId }: { gameId: number }) {
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
  
  // Create mock team objects to pass to child components if necessary
  const away = { name: game.away_name, abbr: game.away_abbr.toLowerCase() };
  const home = { name: game.home_name, abbr: game.home_abbr.toLowerCase() };
  const realAway = teams.find(t => t.abbr === away.abbr) || teams[0];
  const realHome = teams.find(t => t.abbr === home.abbr) || teams[0];

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

const POSITION_LABELS: Record<string, string> = {
  QB: "Quarterback", HB: "Halfback", FB: "Fullback", WR: "Wide Receiver", TE: "Tight End",
  LT: "Left Tackle", LG: "Left Guard", C: "Center", RG: "Right Guard", RT: "Right Tackle",
  LEDG: "Edge Rusher", REDG: "Edge Rusher", DT: "Defensive Tackle", MIKE: "Middle Linebacker",
  WILL: "Weakside Linebacker", SAM: "Strongside Linebacker", CB: "Cornerback", FS: "Free Safety",
  SS: "Strong Safety", K: "Kicker", P: "Punter", LS: "Long Snapper",
};

// Player-page columns for units without offensive box-score lines (from the Season Stats capture, which lists
// only the leaders in each category; "—" = not listed, not zero).
type StatCol = { label: string; value: (row: any) => any };
const dash = (v: any) => (v === null || v === undefined ? "—" : v);
const ratio = (made: any, att: any) => (att === null || att === undefined ? "—" : `${made ?? 0}/${att}`);
const unitGameColumns: Record<string, StatCol[]> = {
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
const unitCareerColumns: Record<string, StatCol[]> = {
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

function PlayerPage({ category, playerId }: { category: string; playerId: number }) {
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

const offensiveStatCategories = ["Passing", "Rushing", "Receiving", "Scoring"];

// Stats page tabs by unit. Defense and special teams come from the Season Stats capture, which lists only the
// leaders in each category (defenders by tackles).
const statCategoryGroups: Record<"team" | "player", { unit: string; categories: string[] }[]> = {
  player: [
    { unit: "Offense", categories: offensiveStatCategories },
    { unit: "Defense", categories: ["Tackles", "Sacks", "Interceptions"] },
    { unit: "Special Teams", categories: ["Kicking", "Punting", "Kick Returns", "Punt Returns"] },
  ],
  team: [
    { unit: "Offense", categories: [...offensiveStatCategories, "Downs"] },
    { unit: "Defense", categories: ["Red Zone Defense"] },
  ],
};

const unitStatColumns: Record<string, string[]> = {
  Tackles: ["TOT", "SOLO", "AST", "TFL", "SCK", "FF", "FR"],
  Sacks: ["SCK", "TFL", "TOT", "FF", "FR", "SFTY"],
  Interceptions: ["INT", "YDS", "AVG", "LNG", "TD", "PD"],
  Kicking: ["FGM", "FGA", "FG%", "LNG", "50+", "XPM", "XPA", "PTS"],
  Punting: ["PUNTS", "YDS", "AVG", "NET", "LNG", "IN 20", "TB"],
  "Kick Returns": ["RET", "YDS", "AVG", "LNG", "TD"],
  "Punt Returns": ["RET", "YDS", "AVG", "LNG", "TD"],
  Downs: ["3D CONV", "3D ATT", "3D%", "4D CONV", "4D ATT", "4D%", "2PT", "RZ ATT", "RZ TD", "RZ TD%", "PEN", "PEN YDS"],
  "Red Zone Defense": ["PA", "PA/G", "RZ ATT", "RZ TD", "RZ FG", "RZ TD%", "RZ SCORE%"],
};

// Postseason tables come from playoff box scores, which record conversions but not attempts and penalty yards
// but not the count (server/api.py team_situational_postseason)
const postseasonStatColumns: Record<string, string[]> = {
  Downs: ["3D CONV", "4D CONV", "2PT", "RZ ATT", "RZ TD", "RZ TD%", "PEN YDS"],
};

const statColumns: Record<string, string[]> = {
  Passing: ["ATT", "CMP", "CMP %", "YDS/ATT", "PASS YDS", "TD", "INT", "RATE", "1ST", "1ST%", "20+", "40+", "LNG", "SCK"],
  Rushing: ["ATT", "YDS", "AVG", "LNG", "TD", "1ST", "1ST%", "20+", "FUM"],
  Receiving: ["REC", "TGT", "YDS", "AVG", "LNG", "TD", "1ST", "20+", "YAC"],
  Scoring: ["PTS", "TD", "PASS TD", "RUSH TD", "REC TD", "FG", "XP", "2PT"],
  Downs: ["1ST", "PASS 1ST", "RUSH 1ST", "3D ATT", "3D CONV", "3D%", "4D ATT", "4D%"],
  Tackles: ["TOT", "SOLO", "AST", "TFL", "QB HIT", "FF", "FR"],
  Sacks: ["SCK", "SCK YDS", "QB HIT", "TFL", "FF", "PRESSURES"],
  Interceptions: ["INT", "YDS", "AVG", "LNG", "TD", "PD"],
  Coverage: ["TGT", "CMP", "CMP%", "YDS", "TD", "PD", "RATE"],
  Kicking: ["FGM", "FGA", "FG%", "LNG", "XPM", "XPA", "PTS"],
  Punting: ["PUNTS", "YDS", "AVG", "NET", "LNG", "IN 20", "TB"],
  "Kick Returns": ["RET", "YDS", "AVG", "LNG", "TD"],
  "Punt Returns": ["RET", "YDS", "AVG", "LNG", "TD", "FC"],
};

// Seasons before this are reconstructed league history (server/api.py HISTORY_CAPTURED_FROM)
const HISTORY_CAPTURED_FROM = 2035;

function StatsPage({ mode }: { mode: "team" | "player" }) {
  const [category, setCategory] = useState("Passing");
  const systemStatus = useContext(SystemContext);
  const [season, setSeason] = useState(systemStatus.season);
  const [seasonType, setSeasonType] = useState("Regular Season");
  const [statsYearOpen, setStatsYearOpen] = useState(false);
  const [statsSeasonOpen, setStatsSeasonOpen] = useState(false);
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({ key: "rank", direction: "asc" });
  const [page, setPage] = useState(1);
  const [statsData, setStatsData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/api/stats/${mode}?season=${season}&category=${encodeURIComponent(category)}&season_type=${seasonType === "Postseason" ? "postseason" : "regular"}`)
      .then(res => res.json())
      .then(data => {
        setStatsData(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(() => {
        setStatsData([]);
        setLoading(false);
      });
  }, [mode, season, category, seasonType]);

  const unit = statCategoryGroups[mode].find((group) => group.categories.includes(category))?.unit ?? "Offense";
  const listedLeadersOnly = mode === "player" && unit !== "Offense";
  const updated = formatUpdated(systemStatus.last_updated);
  const allColumns = unitStatColumns[category]
    ? unitStatColumns[category]
    : mode === "player" && category === "Scoring"
    ? ["PASSING TD", "RUSHING TD", "RECEIVING TD", "TOTAL TD"]
    : category === "Passing"
      ? ["ATT", "CMP", "CMP %", "YDS/ATT", "PASS YDS", "TD", "INT", "RATE"]
      : category === "Rushing"
        ? ["ATT", "YDS", "AVG", "TD", "FUM"]
        : category === "Receiving"
          ? ["REC", "YDS", "AVG", "TD"]
          : ["PTS", "TOTAL TD", "PASS TD", "RUSH TD"];
  // Postseason: only the fields playoff box scores record (and of those, the ones the API returned)
  const returned = statsData[0]?.stats;
  const postseasonColumns = postseasonStatColumns[category] ?? allColumns;
  const columns = seasonType !== "Postseason" ? allColumns
    : returned ? postseasonColumns.filter((column) => column in returned) : postseasonColumns;

  const sortedRows = [...statsData].sort((a, b) => {
    let first: any;
    let second: any;
    if (sort.key === "rank") {
      return 0;
    } else if (sort.key === "name") {
      first = mode === "team" ? a.team_name : a.name ?? "";
      second = mode === "team" ? b.team_name : b.name ?? "";
    } else if (sort.key === "team") {
      first = a.team_name;
      second = b.team_name;
    } else if (sort.key === "position") {
      first = a.position ?? "";
      second = b.position ?? "";
    } else {
      const valA = a.stats?.[sort.key];
      const valB = b.stats?.[sort.key];
      first = typeof valA === "number" ? valA : parseFloat(String(valA || "").replace(/[^0-9.-]/g, "")) || 0;
      second = typeof valB === "number" ? valB : parseFloat(String(valB || "").replace(/[^0-9.-]/g, "")) || 0;
    }

    const comparison = typeof first === "string"
      ? first.localeCompare(String(second))
      : (first || 0) - (second || 0);

    return sort.direction === "asc" ? comparison : -comparison;
  });

  const pageSize = 20;
  const pageCount = mode === "player" ? Math.max(1, Math.ceil(sortedRows.length / pageSize)) : 1;
  const visibleRows = mode === "player"
    ? sortedRows.slice((page - 1) * pageSize, page * pageSize)
    : sortedRows;

  const toggleSort = (key: string) => {
    setPage(1);
    setSort((current) => ({
      key,
      direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
    }));
  };

  const sortIndicator = (key: string) => sort.key === key ? (sort.direction === "asc" ? "↑" : "↓") : "↕";

  return (
    <main className="stats-page">
      <section className="stats-masthead">
        <div>
          <span>LEAGUE DATA CENTER</span>
          <h1>{mode === "team" ? "Team" : "Player"} Statistics</h1>
          <p>Verified performance data from across the simulated league.</p>
        </div>
      </section>

      <section className="stats-workspace">
        <div className="stats-heading">
          <span>{season} {mode.toUpperCase()} STATS · {unit.toUpperCase()}</span>
          <h2>{category}</h2>
        </div>
        {season < HISTORY_CAPTURED_FROM && (
          <p className="player-log-note stats-history-note">
            <span className="career-history-tag">hist</span> {season} is reconstructed league history: season totals
            only{mode === "team" ? " (team rows are their players' lines summed, so depth players who weren't tracked are missing)" : ""};
            no postseason or situational stats were kept.
          </p>
        )}

        <div className="stat-category-tabs">
          {statCategoryGroups[mode].map((group) => (
            <div className="stat-category-group" key={group.unit}>
              <small>{group.unit}</small>
              {group.categories.map((item) => (
                <button className={category === item ? "active" : ""} onClick={() => { setCategory(item); setPage(1); setSort({ key: "rank", direction: "asc" }); }} key={item}>{item}</button>
              ))}
            </div>
          ))}
        </div>

        <div className="stats-filter-bar">
          <div className="stats-filter-controls">
            <div className="standings-filter-dropdown stats-year-dropdown" onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setStatsYearOpen(false);
            }}>
              <span>Year</span>
              <button className={statsYearOpen ? "open" : ""} onClick={() => { setStatsYearOpen(!statsYearOpen); setStatsSeasonOpen(false); }} aria-expanded={statsYearOpen}>
                <strong>{season}</strong><Chevron />
              </button>
              {statsYearOpen && (
                <div className="standings-filter-menu">
                  {systemStatus.availableSeasons.map((item) => (
                    <button className={season === item ? "selected" : ""} onClick={() => { setSeason(item); setPage(1); setStatsYearOpen(false); }} key={item}>
                      <span>{item}</span><small>{item === systemStatus.season ? "Current" : "Season"}</small>{season === item && <i>✓</i>}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="standings-filter-dropdown stats-season-type" onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setStatsSeasonOpen(false);
            }}>
              <span>Season</span>
              <button className={statsSeasonOpen ? "open" : ""} onClick={() => { setStatsSeasonOpen(!statsSeasonOpen); setStatsYearOpen(false); }} aria-expanded={statsSeasonOpen}>
                <strong>{seasonType}</strong><Chevron />
              </button>
              {statsSeasonOpen && (
                <div className="standings-filter-menu">
                  {["Regular Season", "Postseason"].map((item) => (
                    <button className={seasonType === item ? "selected" : ""} onClick={() => { setSeasonType(item); setPage(1); setStatsSeasonOpen(false); }} key={item}>
                      <span>{item}</span><small>{item === "Regular Season" ? "Weeks 1–18" : "Playoffs"}</small>{seasonType === item && <i>✓</i>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          {updated && <div className="stats-updated"><span>Updated</span><strong>{updated}</strong></div>}
        </div>
      </section>

      <section className="stats-table-section">
        <div className="stats-table-meta">
          <span>{mode === "team" ? "32 TEAMS" : listedLeadersOnly ? "LEAGUE LEADERS (PLAYERS THE LEAGUE LISTS)" : "QUALIFIED PLAYERS"}</span>
          <span>{seasonType.toUpperCase()}</span>
        </div>
        <div className="stats-table-scroll">
          <table className="league-stats-table">
            <thead>
              <tr>
                <th aria-sort={sort.key === "rank" ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                  <button onClick={() => toggleSort("rank")}>RK <i>{sortIndicator("rank")}</i></button>
                </th>
                <th aria-sort={sort.key === "name" ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                  <button onClick={() => toggleSort("name")}>{mode === "team" ? "TEAM" : "PLAYER"} <i>{sortIndicator("name")}</i></button>
                </th>
                {mode === "player" && <>
                  <th aria-sort={sort.key === "team" ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                    <button onClick={() => toggleSort("team")}>TEAM <i>{sortIndicator("team")}</i></button>
                  </th>
                  <th aria-sort={sort.key === "position" ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                    <button onClick={() => toggleSort("position")}>POS <i>{sortIndicator("position")}</i></button>
                  </th>
                </>}
                {columns.map((column) => {
                  return (
                    <th aria-sort={sort.key === column ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} key={column}>
                      <button onClick={() => toggleSort(column)}>{column} <i>{sortIndicator(column)}</i></button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={columns.length + (mode === "player" ? 4 : 2)} style={{ textAlign: "center", padding: "3rem", color: "var(--gray-500)" }}>
                    Loading {category} statistics...
                  </td>
                </tr>
              ) : visibleRows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length + (mode === "player" ? 4 : 2)} style={{ textAlign: "center", padding: "3rem", color: "var(--gray-500)" }}>
                    {seasonType === "Postseason"
                      ? `No postseason ${category} stats recorded for ${season} yet.`
                      : `No statistics found for ${category} in ${season}.`}
                  </td>
                </tr>
              ) : (
                visibleRows.map((row, rowIndex) => {
                  const rankNumber = (mode === "player" ? (page - 1) * pageSize : 0) + rowIndex + 1;
                  return (
                    <tr key={row.id ?? rowIndex}>
                      <td>{String(rankNumber).padStart(2, "0")}</td>
                      <td>
                        {mode === "team" ? (
                          <a href={`#team-${row.team_abbr.toLowerCase()}`}>
                            <img src={logo(row.team_abbr.toLowerCase())} alt="" />
                            <strong>{row.team_name}</strong>
                          </a>
                        ) : (
                          <a className="stats-player" href={`#player-${row.id}`}>
                            <span className="player-number">{row.player_number}</span>
                            <strong>{row.name}</strong>
                          </a>
                        )}
                      </td>
                      {mode === "player" && (
                        <>
                          <td>
                            <a className="player-team-cell" href={`#team-${row.team_abbr.toLowerCase()}`}>
                              <img className="table-team-logo" src={logo(row.team_abbr.toLowerCase())} alt="" />
                              {row.team_name}
                            </a>
                          </td>
                          <td>{row.position}</td>
                        </>
                      )}
                      {columns.map((column) => (
                        <td key={column}>{row.stats?.[column] ?? "—"}</td>
                      ))}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {mode === "player" && !loading && sortedRows.length > pageSize && (
          <div className="stats-pagination">
            <span>Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, sortedRows.length)} of {sortedRows.length} players</span>
            <div>
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1} aria-label="Previous page"><Chevron direction="left" /></button>
              {Array.from({ length: pageCount }, (_, index) => index + 1).map((item) => (
                <button className={page === item ? "active" : ""} onClick={() => setPage(item)} key={item}>{item}</button>
              ))}
              <button onClick={() => setPage(Math.min(pageCount, page + 1))} disabled={page === pageCount} aria-label="Next page"><Chevron direction="right" /></button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

function StandingsPage() {
  const [view, setView] = useState<"division" | "conference" | "league">("division");
  const systemStatus = useContext(SystemContext);
  const [year, setYear] = useState(systemStatus.season);
  const [seasonType, setSeasonType] = useState("Regular Season");
  const [yearOpen, setYearOpen] = useState(false);
  const [seasonTypeOpen, setSeasonTypeOpen] = useState(false);

  const [standingsData, setStandingsData] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/standings/${year}`)
      .then(r => r.json())
      .then(data => setStandingsData(data));
  }, [year]);

  const standingFor = (team: Team) => {
    const raw = standingsData.find(s => s.abbr.toLowerCase() === team.abbr.toLowerCase()) || {
      wins: 0, losses: 0, ties: 0, pointsFor: 0, pointsAgainst: 0,
      homeWins: 0, homeLosses: 0, awayWins: 0, awayLosses: 0,
      streak: "0", lastFive: "0-0"
    };
    const wins = raw.wins;
    const losses = raw.losses;
    const ties = raw.ties;
    const pct = (wins + (ties * 0.5)) / Math.max(1, wins + losses + ties);
    // A 2026-2034 history season has records but no games: splits, streak and last five read "—"
    if (raw.isHistory) {
      return {
        team, wins, losses, ties, pct: pct.toFixed(3).replace(/^0\./, "."),
        pointsFor: raw.pointsFor, pointsAgainst: raw.pointsAgainst,
        differential: raw.pointsFor - raw.pointsAgainst,
        home: "—", away: "—", division: "—", conference: "—", streak: "—", lastFive: "—",
        divisionPlace: raw.divisionPlace as number | null, seed: raw.seed as number | null,
        playoffResult: raw.playoffResult as string | null, history: true,
      };
    }
    
    // We mock division and conference records for now since the API doesn't compute them
    const index = teams.indexOf(team);
    
    return {
      team, wins, losses, ties, pct: pct.toFixed(3).replace(/^0\./, "."), 
      pointsFor: raw.pointsFor, pointsAgainst: raw.pointsAgainst,
      differential: raw.pointsFor - raw.pointsAgainst,
      home: `${raw.homeWins}–${raw.homeLosses}${raw.ties ? '–'+raw.ties : ''}`,
      away: `${raw.awayWins}–${raw.awayLosses}${raw.ties ? '–'+raw.ties : ''}`,
      division: `${raw.divWins || 0}–${raw.divLosses || 0}${raw.divTies ? '–'+raw.divTies : ''}`,
      conference: `${raw.confWins || 0}–${raw.confLosses || 0}${raw.confTies ? '–'+raw.confTies : ''}`,
      streak: raw.streak,
      lastFive: raw.lastFive,
      divisionPlace: null as number | null, seed: null as number | null, playoffResult: null as string | null,
      history: false,
    };
  };
  const historySeason = standingsData.some((s) => s.isHistory);

  const [sortState, setSortState] = useState<Record<string, { key: string, dir: "desc" | "asc" }>>({});

  const toggleSort = (groupLabel: string, key: string) => {
    setSortState(prev => {
      const current = prev[groupLabel] || { key: "W", dir: "desc" };
      if (current.key === key) {
        return { ...prev, [groupLabel]: { key, dir: current.dir === "desc" ? "asc" : "desc" } };
      }
      return { ...prev, [groupLabel]: { key, dir: key === "TEAM" ? "asc" : "desc" } };
    });
  };

  const sortIndicator = (groupLabel: string, key: string) => {
    const current = sortState[groupLabel] || { key: "W", dir: "desc" };
    return current.key === key ? (current.dir === "asc" ? "↑" : "↓") : "↕";
  };

  const parseRecordWins = (record: string) => {
    if (!record || record === "0") return 0;
    const parts = record.includes("–") ? record.split("–") : record.split("-");
    return parseInt(parts[0]) || 0;
  };

  const parseStreak = (streak: string) => {
    if (!streak || streak === "0") return 0;
    const val = parseInt(streak);
    return streak.includes("W") ? val : -val;
  };

  const sortedTeams = (members: Team[], groupLabel: string) => {
    const currentSort = sortState[groupLabel] || { key: "W", dir: "desc" };
    const { key: sortKey, dir: sortDir } = currentSort;

    return [...members].sort((a, b) => {
      const sA = standingFor(a);
      const sB = standingFor(b);
      
      if (sortKey === "TEAM") { 
        return sortDir === "asc" ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
      }

      let valA: any = sA.wins;
      let valB: any = sB.wins;

      if (sortKey === "W") { valA = sA.wins; valB = sB.wins; }
      else if (sortKey === "L") { valA = sA.losses; valB = sB.losses; }
      else if (sortKey === "T") { valA = sA.ties; valB = sB.ties; }
      else if (sortKey === "PCT") { valA = Number(sA.pct); valB = Number(sB.pct); }
      else if (sortKey === "PF") { valA = sA.pointsFor; valB = sB.pointsFor; }
      else if (sortKey === "PA") { valA = sA.pointsAgainst; valB = sB.pointsAgainst; }
      else if (sortKey === "DIFF") { valA = sA.differential; valB = sB.differential; }
      else if (sortKey === "HOME") { valA = parseRecordWins(sA.home); valB = parseRecordWins(sB.home); }
      else if (sortKey === "AWAY") { valA = parseRecordWins(sA.away); valB = parseRecordWins(sB.away); }
      else if (sortKey === "DIV") { valA = parseRecordWins(sA.division); valB = parseRecordWins(sB.division); }
      else if (sortKey === "CONF") { valA = parseRecordWins(sA.conference); valB = parseRecordWins(sB.conference); }
      else if (sortKey === "STRK") { valA = parseStreak(sA.streak); valB = parseStreak(sB.streak); }
      else if (sortKey === "LAST 5") { valA = parseRecordWins(sA.lastFive); valB = parseRecordWins(sB.lastFive); }

      const comparison = valA - valB;
      // history seasons: a tied record keeps the generated division order (no games to break it)
      if (comparison === 0 && sA.divisionPlace != null && sB.divisionPlace != null && sA.divisionPlace !== sB.divisionPlace
          && view === "division") {
        return sA.divisionPlace - sB.divisionPlace;
      }
      if (comparison === 0) {
        if (sA.differential !== sB.differential) {
          return sortDir === "desc" ? sB.differential - sA.differential : sA.differential - sB.differential;
        }
        return sortDir === "desc" ? sB.wins - sA.wins : sA.wins - sB.wins;
      }
      return sortDir === "desc" ? valB - valA : valA - valB;
    });
  };

  const divisionGroups = Object.entries(divisions).map(([label, abbreviations]) => ({
    label,
    teams: abbreviations.map((abbr) => teams.find((team) => team.abbr === abbr) as Team),
  }));
  const conferenceGroups = ["AFC", "NFC"].map((conference) => ({
    label: `${conference} Conference`,
    teams: teams.filter((team) => getDivision(team.abbr).startsWith(conference)),
  }));
  const groups = view === "division"
    ? divisionGroups
    : view === "conference"
      ? conferenceGroups
      : [{ label: "National Football League", teams: teams }];

  return (
    <main className="standings-page">
      <section className="standings-masthead">
        <div><span>LEAGUE TABLE</span><h1>Standings</h1><p>The complete {year} postseason picture, from every division to the full league.</p></div>
      </section>

      <section className="standings-controls">
        <div className="standings-view-tabs">
          {(["division", "conference", "league"] as const).map((item) => (
            <button className={view === item ? "active" : ""} onClick={() => setView(item)} key={item}>{item}</button>
          ))}
        </div>
        <div className="standings-filters">
          <div className="standings-filter-dropdown" onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setYearOpen(false);
          }}>
            <span>Year</span>
            <button className={yearOpen ? "open" : ""} onClick={() => { setYearOpen(!yearOpen); setSeasonTypeOpen(false); }} aria-expanded={yearOpen}>
              <strong>{year}</strong><Chevron />
            </button>
            {yearOpen && (
              <div className="standings-filter-menu">
                {systemStatus.availableSeasons.map((item) => (
                  <button className={year === item ? "selected" : ""} onClick={() => { setYear(item); setYearOpen(false); }} key={item}>
                    <span>{item}</span><small>{item === systemStatus.season ? "Current" : "Season"}</small>{year === item && <i>✓</i>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="standings-filter-dropdown season-type-dropdown" onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setSeasonTypeOpen(false);
          }}>
            <span>Season</span>
            <button className={seasonTypeOpen ? "open" : ""} onClick={() => { setSeasonTypeOpen(!seasonTypeOpen); setYearOpen(false); }} aria-expanded={seasonTypeOpen}>
              <strong>{seasonType}</strong><Chevron />
            </button>
            {seasonTypeOpen && (
              <div className="standings-filter-menu">
                {["Regular Season", "Postseason"].map((item) => (
                  <button className={seasonType === item ? "selected" : ""} onClick={() => { setSeasonType(item); setSeasonTypeOpen(false); }} key={item}>
                    <span>{item}</span><small>{item === "Regular Season" ? "Weeks 1–18" : "Playoffs"}</small>{seasonType === item && <i>✓</i>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      {historySeason && (
        <p className="standings-history-note">
          <span className="career-history-tag">hist</span> {year} is reconstructed league history: final records, points,
          division order and playoff seeds are canon; no games were kept, so splits and streaks read —.
        </p>
      )}
      <section className="standings-tables">
        {groups.map((group, groupIndex) => {
          const conference = group.label.startsWith("AFC") ? "American Football Conference" : group.label.startsWith("NFC") ? "National Football Conference" : null;
          const showConference = view === "division" && (groupIndex === 0 || groupIndex === 4);
          const currentSort = sortState[group.label] || { key: "W", dir: "desc" };
          return (
            <div className="standings-group-wrap" key={group.label}>
              {showConference && <div className="conference-banner">{conference}</div>}
              <div className="standings-table-scroll">
                <table className="standings-table">
                  <thead>
                    <tr>
                      <th aria-sort={currentSort.key === "TEAM" ? (currentSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                        <button onClick={() => toggleSort(group.label, "TEAM")}>{group.label} <i>{sortIndicator(group.label, "TEAM")}</i></button>
                      </th>
                      {["W", "L", "T", "PCT", "PF", "PA", "DIFF", "HOME", "AWAY", "DIV", "CONF", "STRK", "LAST 5"].map(col => (
                        <th key={col} aria-sort={currentSort.key === col ? (currentSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                          <button onClick={() => toggleSort(group.label, col)}>{col} <i>{sortIndicator(group.label, col)}</i></button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTeams(group.teams, group.label).map((team, index) => {
                      const standing = standingFor(team);
                      return (
                        <tr key={team.abbr}>
                          <td><a href={`#team-${team.abbr}`}><span>{index + 1}</span><img src={logo(team.abbr)} alt="" /><strong>{team.name}</strong>
                            {standing.seed ? <small className="standings-seed" title={standing.playoffResult === "CHAMPION" ? "Won the Super Bowl" : "Playoff seed"}>{standing.playoffResult === "CHAMPION" ? "🏆" : ""}{standing.seed}</small> : null}</a></td>
                          <td><b>{standing.wins}</b></td><td>{standing.losses}</td><td>{standing.ties}</td><td><b>{standing.pct}</b></td>
                          <td>{standing.pointsFor}</td><td>{standing.pointsAgainst}</td><td className={standing.differential >= 0 ? "positive" : "negative"}>{standing.differential > 0 ? "+" : ""}{standing.differential}</td>
                          <td>{standing.home}</td><td>{standing.away}</td><td>{standing.division}</td><td>{standing.conference}</td><td>{standing.streak}</td><td>{standing.lastFive}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </section>
    </main>
  );
}

type PowerRankingEntry = {
  rank: number;
  previous_rank: number | null;
  movement: number | null;
  record: string | null;
  kicker: string | null;
  blurb: string | null;
  team_id: number;
  abbr: string;
  name: string;
  city: string;
  team_color: string | null;
  division: string | null;
};

type PowerRankingsResponse = {
  season: number;
  week: number | null;
  available_weeks: number[];
  article: { id: number; headline: string; excerpt: string | null; persona: string | null } | null;
  rankings: PowerRankingEntry[];
};

const writerName = (persona: string | null | undefined) =>
  (persona || "").toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

function usePowerRankings(week?: number | null) {
  const systemStatus = useContext(SystemContext);
  const [data, setData] = useState<PowerRankingsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const weekParam = week ? `&week=${week}` : "";
    fetch(`${API_BASE}/api/power_rankings?season=${systemStatus.season}${weekParam}`)
      .then((res) => res.json())
      .then((json) => setData(json))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [systemStatus.season, week]);

  return { data, loading };
}

function PowerRankingsPage() {
  const [rankingWeek, setRankingWeek] = useState<number | null>(null);
  const { data, loading } = usePowerRankings(rankingWeek);
  const rankings = data?.rankings || [];
  const shownWeek = data?.week ?? rankingWeek;

  return (
    <main className="rankings-page">
      <section className="rankings-masthead">
        <div>
          <span>GRIDIRON POWER INDEX</span>
          <h1>Power Rankings</h1>
          <p>{data?.article?.headline || "Every team, every week—ranked by form, underlying performance and postseason outlook."}</p>
        </div>
      </section>

      <section className="rankings-navigation">
        <div className="rankings-week-nav">
          <span>{(data?.available_weeks.length || 0) > 0 ? "Published rankings" : "No rankings published yet"}</span>
          {(data?.available_weeks || []).map((week) => (
            <button className={shownWeek === week ? "active" : ""} onClick={() => setRankingWeek(week)} key={week}>Week {week}</button>
          ))}
        </div>
        {rankings.length > 0 && (
          <div className="rankings-jump">
            <span>Jump to a team</span>
            <div>
              {rankings.map((team) => (
                <button onClick={() => document.getElementById(`rank-${team.abbr.toLowerCase()}`)?.scrollIntoView({ behavior: "smooth", block: "start" })} key={team.abbr}>
                  <img src={logo(team.abbr)} alt="" />{team.abbr}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="rankings-list">
        <div className="rankings-list-head">
          <span>{shownWeek ? `WEEK ${shownWeek}` : "POWER RANKINGS"}</span>
          <strong>THE FULL 32</strong>
          <small>{data?.article?.persona ? `By ${writerName(data.article.persona)}` : ""}</small>
        </div>
        {loading && <article className="ranking-entry"><div className="ranking-copy"><p>Loading rankings…</p></div></article>}
        {!loading && rankings.length === 0 && (
          <article className="ranking-entry">
            <div className="ranking-copy">
              <h3>No Power Rankings for this week yet</h3>
              <p>Rankings appear here once the weekly Power Rankings column is published with all 32 teams.</p>
            </div>
          </article>
        )}
        {!loading && rankings.map((team) => {
          const abbr = team.abbr.toLowerCase();
          const fullName = `${team.city} ${team.name}`;
          const movement = team.movement;
          return (
            <article className="ranking-entry" id={`rank-${abbr}`} key={team.team_id} style={team.team_color ? ({ "--team-color": team.team_color } as CSSProperties) : undefined}>
              <div className="ranking-number">{String(team.rank).padStart(2, "0")}</div>
              <div className="ranking-team-lockup">
                <img src={logo(team.abbr)} alt={`${fullName} logo`} />
                <div>
                  <a href={`#team-${abbr}`}><h2>{fullName}</h2></a>
                  <span>{team.record || "—"} · {team.division || getDivision(abbr)}</span>
                </div>
                <div className={`ranking-movement ${(movement ?? 0) >= 0 ? "up" : "down"}`}>
                  <small>LAST WEEK</small>
                  <strong>{team.previous_rank ?? "—"}</strong>
                  <i>{movement == null ? "NEW" : movement === 0 ? "—" : movement > 0 ? `↑ ${movement}` : `↓ ${Math.abs(movement)}`}</i>
                </div>
              </div>
              <div className="ranking-result">
                <span>RECORD ENTERING WEEK {shownWeek}</span>
                <strong>{team.record || "—"}</strong>
              </div>
              <div className="ranking-copy">
                {team.kicker && <h3>{team.kicker}</h3>}
                <p>{team.blurb}</p>
                {data?.article?.persona && <small>Analysis by {writerName(data.article.persona)} · National Reporter</small>}
              </div>
            </article>
          );
        })}
      </section>
    </main>
  );
}

// Inline markdown: **bold**, *italic*, `code`, [link](url). Built as React nodes.
function articleInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      out.push(<strong key={m.index}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      out.push(<em key={m.index}>{m[2]}</em>);
    } else if (m[3] !== undefined) {
      out.push(<code key={m.index}>{m[3]}</code>);
    } else if (m[4] !== undefined) {
      out.push(
        <a key={m.index} href={m[5]} target={m[5].startsWith("#") ? undefined : "_blank"} rel="noreferrer">
          {m[4]}
        </a>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Markdown renderer for newsroom articles: headings, lists, blockquotes, tables, dividers, and paragraphs.
function ArticleMarkdown({ content }: { content: string }) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let firstParagraph = true;

  const flush = () => {
    if (!para.length) return;
    const text = para.join(" ").trim();
    para = [];
    if (!text) return;
    blocks.push(
      <p key={blocks.length} className={firstParagraph ? "article-lead" : undefined}>
        {articleInline(text)}
      </p>
    );
    firstParagraph = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }
    // Skip H1 title and excerpt as they are already displayed in the article header
    if (/^#\s/.test(line) || /^>\s*\*\*EXCERPT:\*\*/i.test(line) || /^\*\*EXCERPT:\*\*/i.test(line)) {
      flush();
      continue;
    }
    // Horizontal rule: --- or ***
    if (/^(-{3,}|\*{3,})$/.test(line)) {
      flush();
      blocks.push(<hr key={blocks.length} />);
      continue;
    }
    // Headings ##, ###, ####
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      if (level === 2) {
        blocks.push(<h2 key={blocks.length}>{articleInline(heading[2])}</h2>);
      } else if (level === 3) {
        blocks.push(<h3 key={blocks.length}>{articleInline(heading[2])}</h3>);
      } else {
        blocks.push(<h4 key={blocks.length}>{articleInline(heading[2])}</h4>);
      }
      continue;
    }
    // Blockquote: > quote
    if (line.startsWith(">")) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      i--;
      blocks.push(
        <blockquote key={blocks.length}>
          <span>“</span>
          {articleInline(quote.join(" "))}
        </blockquote>
      );
      continue;
    }
    // Table: | col | col |
    if (line.startsWith("|")) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const [head, ...body] = rows;
      blocks.push(
        <div className="article-table-wrap" key={blocks.length}>
          <table>
            {head && (
              <thead>
                <tr>
                  {head.map((c, k) => (
                    <th key={k}>{articleInline(c)}</th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {body.map((r, k) => (
                <tr key={k}>
                  {r.map((c, j) => (
                    <td key={j}>{articleInline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }
    // Lists: bullet or numbered
    const isBullet = /^[-*•]\s+/.test(line);
    const isNumber = /^\d+[.)]\s+/.test(line);
    if (isBullet || isNumber) {
      flush();
      const items: string[] = [];
      const itemRe = isBullet ? /^[-*•]\s+/ : /^\d+[.)]\s+/;
      while (i < lines.length) {
        const curLine = lines[i].trim();
        if (itemRe.test(curLine)) {
          items.push(curLine.replace(itemRe, ""));
        } else if (curLine && (lines[i].startsWith("  ") || lines[i].startsWith("\t"))) {
          if (items.length) {
            items[items.length - 1] += " " + curLine;
          } else {
            break;
          }
        } else {
          break;
        }
        i++;
      }
      i--;
      const children = items.map((it, k) => <li key={k}>{articleInline(it)}</li>);
      blocks.push(isNumber ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
      continue;
    }

    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}

function ArticlePage({ articleId }: { articleId: string }) {
  const systemStatus = useContext(SystemContext);
  const [dbArticle, setDbArticle] = useState<any>(null);
  const isDbArticle = /^\d+$/.test(articleId);

  useEffect(() => {
    if (isDbArticle) {
      fetch(`${API_BASE}/api/news/article/${articleId}`)
        .then(res => res.json())
        .then(data => {
          if (!data.error) setDbArticle(data);
        })
        .catch(err => console.error("Error loading article:", err));
    }
  }, [articleId, isDbArticle]);

  const nationalMatch = articleId.match(/^national-(\d+)$/);
  const localMatch = articleId.match(/^local-([a-z]+)$/);
  const teamHeroMatch = articleId.match(/^team-hero-([a-z]+)$/);
  const teamStoryMatch = articleId.match(/^team-([a-z]+)-(\d+)$/);
  const playerStoryMatch = articleId.match(/^player-(passing|rushing|receiving|scoring|tightend|fullback)-(\d+)-(\d+)$/);
  let data = {
    category: "The Big Read",
    title: "One night. Two contenders. Everything on the line.",
    dek: "The league's most explosive offense meets the defense built to stop it. Inside the tactical battle that will define the season.",
    author: "Bill Barnwell",
    role: "National Writer",
    initials: "BB",
    subject: "the league's defining matchup",
    image: "https://images.unsplash.com/photo-1634306320557-4c4e4660692f?auto=format&fit=crop&w=1800&q=88",
    body: null as string | null,
    teams: [] as any[],
    players: [] as any[],
    authorHandle: null as string | null | undefined,
    authorBio: null as string | null | undefined,
    related: [] as any[],
  };

  if (isDbArticle && dbArticle) {
    const authorName = dbArticle.author?.display_name || dbArticle.persona || "Staff Writer";
    const initials = authorName.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "NW";
    const primaryTeam = dbArticle.tagged_teams?.find((t: any) => t.mention_type === 'PRIMARY') || dbArticle.tagged_teams?.[0];
    const catLabel = dbArticle.article_type ? dbArticle.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "Analysis";
    data = {
      ...data,
      category: catLabel,
      title: dbArticle.headline || data.title,
      dek: dbArticle.excerpt || data.dek,
      author: authorName,
      authorHandle: dbArticle.author?.handle,
      authorBio: dbArticle.author?.bio,
      role: dbArticle.author?.archetype ? `${dbArticle.author.archetype.replace(/_/g, " ")}` : "Staff Writer",
      initials,
      subject: primaryTeam ? primaryTeam.name : "the league",
      body: dbArticle.content || "",
      teams: dbArticle.tagged_teams || [],
      players: dbArticle.tagged_players || [],
      related: dbArticle.related_articles || [],
    };
  } else if (nationalMatch) {
    const story = stories[Number(nationalMatch[1])] ?? stories[0];
    data = { ...data, category: story.kicker, title: story.title, dek: story.blurb, author: story.author, authorHandle: null, authorBio: null, role: "National Reporter", initials: story.initials, subject: story.title.toLowerCase(), related: [] };
  } else if (localMatch) {
    const story = localStories.find((item) => item.team === localMatch[1]) ?? localStories[0];
    const team = teams.find((item) => item.abbr === story.team) ?? teams[0];
    data = { ...data, category: story.tag, title: story.title, dek: `Inside the decisions, matchups and personalities shaping the latest chapter for ${team.name}.`, author: story.author, authorHandle: null, authorBio: null, role: `${team.name} Beat Reporter`, initials: story.author.split(" ").map((part) => part[0]).join(""), subject: team.name, related: [] };
  } else if (teamHeroMatch || teamStoryMatch) {
    const abbr = teamHeroMatch?.[1] ?? teamStoryMatch?.[1] ?? "sf";
    const team = teams.find((item) => item.abbr === abbr) ?? teams[0];
    const storyIndex = Number(teamStoryMatch?.[2] ?? 0);
    const titles = [
      `Inside the belief driving ${team.name} into the stretch run`,
      `The adjustment that could unlock the ${team.name} postseason run`,
      `Three keys for ${team.name} heading into Week 17`,
      `Why the ${team.name} young core is ahead of schedule`,
    ];
    data = {
      ...data,
      category: teamHeroMatch ? "Exclusive" : "Team Coverage",
      title: titles[storyIndex % titles.length],
      dek: `One locker room, one shared standard and a season still waiting to be defined. A closer look inside ${team.name}.`,
      author: "Jordan Ellis",
      authorHandle: null,
      authorBio: null,
      role: `${team.name} Beat Reporter`,
      initials: "JE",
      subject: team.name,
      image: "https://images.unsplash.com/photo-1704713611761-e0fbbdecc3e9?auto=format&fit=crop&w=1800&q=85",
      related: [],
    };
  } else if (playerStoryMatch) {
    const category = playerStoryMatch[1];
    const playerId = Number(playerStoryMatch[2]);
    const storyIndex = Number(playerStoryMatch[3]);
    const player = getPlayerProfile(category, playerId);
    const titles = [
      `How ${player.name} is changing the geometry of the ${player.team.name} offense`,
      `${player.name}'s preparation is becoming the standard inside the building`,
      `The matchup that could decide ${player.name}'s Week 17 impact`,
      `What the advanced metrics reveal about ${player.name}'s breakout`,
      `${player.name} on leadership, pressure and the postseason chase`,
      `Where ${player.name} ranks among the league's best at ${player.position}`,
    ];
    data = { ...data, category: "Player Coverage", title: titles[storyIndex % titles.length], dek: `The film, numbers and voices behind ${player.name}'s impact on the ${player.team.name} this season.`, author: "Jordan Ellis", authorHandle: null, authorBio: null, role: "National Reporter", initials: "JE", subject: player.name, related: [] };
  }

  return (
    <main className="article-page">
      <article>
        <header className="article-header">
          <div className="article-breadcrumb"><a href="#">The Newsroom</a><span>/</span><b>{data.category}</b></div>
          <span className="article-kicker">{data.category}</span>
          <h1>{data.title}</h1>
          <p>{data.dek}</p>
          <div className="article-byline">
            <span className="avatar red">{data.initials}</span>
            <div>
              <strong>
                {data.authorHandle ? (
                  <a href={`#author-${data.authorHandle.replace(/^@/, '')}`} className="author-byline-link">{data.author}</a>
                ) : data.author}
              </strong>
              <span>{data.role}</span>
            </div>
            <div><small>PUBLISHED</small><b>{dbArticle?.week ? (dbArticle.week === 24 ? `Offseason Edition, ${systemStatus.season} Season` : `Week ${dbArticle.week}, ${systemStatus.season} Season`) : `December 23, ${systemStatus.season} · 9:15 AM ET`}</b></div>
            <div><small>READ TIME</small><b>9 min</b></div>
          </div>
        </header>

        <figure className="article-hero-media">
          <img src={data.image} alt="Football action under stadium lights" />
          <figcaption>Week 17 brings the season's biggest questions into sharper focus. <span>GRIDIRON IMAGES</span></figcaption>
        </figure>

        <div className="article-layout">
          <aside className="article-share">
            <span>SHARE</span>
            <button aria-label="Copy article link">↗</button>
            <button aria-label="Share by email">@</button>
          </aside>
          <div className="article-body">
            {data.body ? (
              <ArticleMarkdown content={data.body} />
            ) : (
              <>
                <p className="article-lead">There is a point late in every season when possibility becomes pressure. For {data.subject}, that moment has arrived—not as a single play or result, but as a collection of details that have started to define everything.</p>
                <p>The evidence appears on film before it reaches the box score. Alignments are cleaner. Decisions arrive a beat faster. The space between a promising idea and a winning one has narrowed, giving coaches confidence to reach deeper into the call sheet when the game demands it.</p>
                <h2>The details behind the rise</h2>
                <p>On early downs, the approach has become more varied without becoming more complicated. Motion reveals coverage. Formations create leverage. The result is an offense playing with answers instead of searching for them after the snap.</p>
                <blockquote><span>“</span>The best teams don't wait for the game to tell them who they are. They impose that identity from the opening series.</blockquote>
                <p>That confidence extends beyond the headline numbers. Teammates point to preparation, communication and a willingness to live with the correct decision even when the outcome is imperfect. Over the course of a long season, those habits compound.</p>
                <h2>What comes next</h2>
                <p>The remaining challenge is translating control into consistency. Opponents will change the picture, test protection rules and force the ball toward uncomfortable matchups. The response will determine whether this run becomes a memorable stretch or something larger.</p>
                <p>For now, the trajectory is clear. The stakes are rising, the margin for error is shrinking and {data.subject} is entering the decisive part of the calendar with a real opportunity to shape what happens next.</p>
              </>
            )}
            <div className="article-author-note">
              <span className="avatar">{data.initials}</span>
              <div>
                <strong>
                  {data.authorHandle ? (
                    <a href={`#author-${data.authorHandle.replace(/^@/, '')}`} className="author-byline-link">{data.author}</a>
                  ) : data.author}
                </strong>
                <p>{data.authorBio || `${data.author} covers the league for Gridiron, specializing in film analysis, roster construction and the stories behind the numbers.`}</p>
              </div>
            </div>
          </div>
          <aside className="article-related">
            <span>KEEP READING</span>
            {((data as any).related && (data as any).related.length > 0 ? (data as any).related.slice(0, 4) : stories.slice(0, 3)).map((story: any, index: number) => {
              const link = story.id ? `#article-${story.id}` : `#article-national-${index}`;
              const title = story.headline || story.title;
              return (
                <a href={link} key={story.id || story.title}>
                  <small>0{index + 1}</small>
                  <strong>{title}</strong>
                  <i>→</i>
                </a>
              );
            })}
          </aside>
        </div>
      </article>
    </main>
  );
}

function AuthorPage({ authorKey }: { authorKey: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/api/author/${encodeURIComponent(authorKey)}`)
      .then(res => res.json())
      .then(json => {
        if (!json.error) setData(json);
        setLoading(false);
      })
      .catch(err => {
        console.error("Error loading author:", err);
        setLoading(false);
      });
  }, [authorKey]);

  if (loading) {
    return (
      <main className="author-page">
        <div className="author-loading">Loading author archive...</div>
      </main>
    );
  }

  if (!data || !data.author) {
    return (
      <main className="author-page">
        <div className="author-not-found">
          <h2>Author not found</h2>
          <p>No profile found for "{authorKey}".</p>
          <a href="#" className="back-link">← Return to Newsroom</a>
        </div>
      </main>
    );
  }

  const { author, articles, total_articles } = data;
  const initials = author.display_name.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "GW";
  const teamColor = author.team?.team_color || "var(--red)";

  return (
    <main className="author-page" style={{ "--author-team-color": teamColor } as any}>
      <header className="author-profile-header">
        <div className="author-profile-main">
          <div className="author-avatar-wrap">
            <span className="avatar author-large-avatar" style={{ background: teamColor }}>{initials}</span>
          </div>
          <div className="author-meta-info">
            <div className="author-role-pills">
              <span className="author-badge">{author.archetype?.replace(/_/g, " ")}</span>
              {author.team && (
                <a href={`#team-${author.team.abbreviation.toLowerCase()}`} className="author-team-badge">
                  {author.team.name}
                </a>
              )}
            </div>
            <h1>{author.display_name}</h1>
            <div className="author-handle-row">
              <span className="author-handle">{author.handle}</span>
              <span className="author-archive-stat"><strong>{total_articles}</strong> Articles in Archive</span>
            </div>
            <p className="author-bio">{author.bio}</p>
          </div>
        </div>
      </header>

      <section className="author-articles-section">
        <div className="author-section-title">
          <span>Writing Archive</span>
          <h2>Published Work ({total_articles})</h2>
        </div>

        {articles && articles.length > 0 ? (
          <div className="author-articles-grid">
            {articles.map((art: any, index: number) => (
              <article className="author-article-card" key={art.id}>
                <div className="author-card-header">
                  <span className="story-number">{index + 1 < 10 ? `0${index + 1}` : index + 1}</span>
                  <span className="kicker">WEEK {art.week} · {art.label}</span>
                </div>
                <div className="author-card-body">
                  <a href={`#article-${art.id}`}>
                    <h3>{art.headline}</h3>
                  </a>
                  <p>{art.excerpt}</p>
                </div>
                <div className="author-card-footer">
                  {art.tagged_teams && art.tagged_teams.length > 0 && (
                    <div className="author-tagged-chips">
                      {art.tagged_teams.slice(0, 3).map((t: any) => (
                        <a href={`#team-${t.abbr.toLowerCase()}`} key={t.id} className="team-chip-tag">
                          {t.abbr}
                        </a>
                      ))}
                    </div>
                  )}
                  <small>Read Article →</small>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="author-empty-archive">
            <p>No articles published yet.</p>
          </div>
        )}
      </section>
    </main>
  );
}

const fantasyArticles = [
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

const fantasyProjections = [
  { name: "J. Chase", position: "WR", team: "CIN", points: "21.4", trend: "up", change: "+1.8", id: "4362628" },
  { name: "S. Barkley", position: "RB", team: "PHI", points: "20.8", trend: "up", change: "+0.9", id: "3929630" },
  { name: "L. Jackson", position: "QB", team: "BAL", points: "20.3", trend: "down", change: "−0.4", id: "3916387" },
  { name: "J. Jefferson", position: "WR", team: "MIN", points: "19.9", trend: "up", change: "+1.2", id: "4262921" },
  { name: "J. Allen", position: "QB", team: "BUF", points: "19.6", trend: "down", change: "−0.7", id: "3918298" },
  { name: "B. Hall", position: "RB", team: "NYJ", points: "18.7", trend: "up", change: "+0.6", id: "4427366" },
];

type FantasyRankingsView = "weekly" | "adp" | "projections" | "ros";

const fantasyRankingPlayers = [
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

type FantasyPlayer = typeof fantasyRankingPlayers[number];

const FantasyPlayerContext = createContext<{
  openPlayer: (player: FantasyPlayer) => void;
} | null>(null);

function useFantasyPlayer() {
  const context = useContext(FantasyPlayerContext);
  if (!context) throw new Error("Fantasy player context is unavailable");
  return context;
}

function FantasyPlayerModal({ player: p, close }: { player: FantasyPlayer; close: () => void }) {
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

function FantasyPlayerProvider({ children }: { children: ReactNode }) {
  const [player, setPlayer] = useState<FantasyPlayer | null>(null);
  return (
    <FantasyPlayerContext.Provider value={{ openPlayer: setPlayer }}>
      {children}
      {player && <FantasyPlayerModal player={player} close={() => setPlayer(null)} />}
    </FantasyPlayerContext.Provider>
  );
}

function FantasyHeader({ active }: { active: "news" | "rankings" }) {
  const systemStatus = useContext(SystemContext);
  const { openPlayer } = useFantasyPlayer();
  const [search, setSearch] = useState("");
  const [allPlayers, setAllPlayers] = useState<any[]>([]);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/fantasy/players?season=${systemStatus.season || 2035}`)
      .then((res) => res.json())
      .then((data) => setAllPlayers(data.players || []))
      .catch(() => setAllPlayers([]));
  }, [systemStatus.season]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === "Escape") {
        setSearch("");
        searchInputRef.current?.blur();
      }
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setSearch("");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const searchClean = search.trim().toLowerCase();
  const matches = searchClean
    ? allPlayers
        .filter((player) =>
          `${player.name} ${player.team} ${player.position}`.toLowerCase().includes(searchClean)
        )
        .slice(0, 8)
    : [];

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && matches.length > 0) {
      e.preventDefault();
      openPlayer(matches[0]);
      setSearch("");
      searchInputRef.current?.blur();
    }
  };

  return (
    <header className="fantasy-header">
      <nav className="fantasy-nav" aria-label="Fantasy navigation">
        <a className="fantasy-brand" href="#fantasy" aria-label="Mu Fantasy Analytics home">
          <span className="fantasy-brand-mark"><i /><b>M</b></span>
          <span><strong>MU FANTASY</strong><small>ANALYTICS</small></span>
        </a>
        <div className="fantasy-nav-links">
          <a className="fantasy-nfl-link" href="#">NFL</a>
          <a className={active === "news" ? "active" : ""} href="#fantasy">News &amp; Articles</a>
          <a className={active === "rankings" ? "active" : ""} href="#fantasy-rankings">Rankings</a>
          <a href="#napper" style={{ color: "var(--napper-teal, #00ceb8)", fontWeight: "700" }}>🛏️ Napper</a>
        </div>
        <div className="fantasy-tools">
          <div className="fantasy-search" ref={searchContainerRef}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
            <input
              ref={searchInputRef}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Search players"
              aria-label="Search fantasy players"
              autoComplete="off"
            />
            <kbd>⌘ K</kbd>
            {search && (
              <div className="fantasy-search-results">
                <span>{matches.length ? `PLAYERS (${matches.length})` : "NO PLAYERS FOUND"}</span>
                {matches.map((player) => (
                  <button
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      openPlayer(player);
                      setSearch("");
                    }}
                    key={player.id}
                  >
                    <i>
                      <img
                        src={getPlayerHeadshot(player)}
                        alt=""
                        onError={(e) => handleHeadshotError(e, player)}
                      />
                    </i>
                    <span><strong>{player.name}</strong><small>{player.position} • {player.team} • {player.fpts} FPTS</small></span>
                    <b>→</b>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="fantasy-profile" aria-label="Open profile menu">MJ</button>
        </div>
      </nav>
    </header>
  );
}

function FantasyRankingsPage({ view }: { view: FantasyRankingsView }) {
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

function FantasyDashboard() {
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

type SocialIconName = "home" | "search" | "bell" | "bookmark" | "desk" | "teams" | "reply" | "repost" | "like" | "chart" | "more";

const fantasyInitials = (name: string) => (name || "").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "MU";

const fantasyDate = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).toUpperCase();
};

// Inline markdown: **bold**, *italic*. Built as React nodes (no HTML injection).
function fantasyInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(m[1] !== undefined ? <strong key={m.index}>{m[1]}</strong> : <em key={m.index}>{m[2]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Article body from the newsroom's markdown: headings, lists, tables, quotes and rules. The H1 and the
// EXCERPT line are shown in the header, so they're skipped here.
function FantasyMarkdown({ content }: { content: string }) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let firstParagraph = true;
  const flush = () => {
    if (!para.length) return;
    const text = para.join(" ").trim();
    para = [];
    if (!text) return;
    blocks.push(<p key={blocks.length} className={firstParagraph ? "fantasy-article-lead" : undefined}>{fantasyInline(text)}</p>);
    firstParagraph = false;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) { flush(); continue; }
    if (/^#\s/.test(line) || /^>\s*\*\*EXCERPT:\*\*/i.test(line)) { flush(); continue; }
    if (/^(-{3,}|\*{3,})$/.test(line)) { flush(); blocks.push(<hr key={blocks.length} />); continue; }
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      blocks.push(level === 2 ? <h2 key={blocks.length}>{fantasyInline(heading[2])}</h2> : <h3 key={blocks.length}>{fantasyInline(heading[2])}</h3>);
      continue;
    }
    if (line.startsWith(">")) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) { quote.push(lines[i].trim().replace(/^>\s?/, "")); i++; }
      i--;
      blocks.push(<blockquote key={blocks.length}>{fantasyInline(quote.join(" "))}</blockquote>);
      continue;
    }
    if (line.startsWith("|")) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const [head, ...body] = rows;
      blocks.push(
        <div className="fantasy-article-table" key={blocks.length}>
          <table>
            {head && <thead><tr>{head.map((c, k) => <th key={k}>{fantasyInline(c)}</th>)}</tr></thead>}
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{fantasyInline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
      continue;
    }
    const isBullet = /^[-*•]\s+/.test(line);
    const isNumber = /^\d+[.)]\s+/.test(line);
    if (isBullet || isNumber) {
      flush();
      const items: string[] = [];
      const itemRe = isBullet ? /^[-*•]\s+/ : /^\d+[.)]\s+/;
      while (i < lines.length && itemRe.test(lines[i].trim())) { items.push(lines[i].trim().replace(itemRe, "")); i++; }
      i--;
      const children = items.map((it, k) => <li key={k}>{fantasyInline(it)}</li>);
      blocks.push(isNumber ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
      continue;
    }
    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}

function FantasyArticlePage({ slug }: { slug: string }) {
  const systemStatus = useContext(SystemContext);
  const isStored = /^\d+$/.test(slug);  // stored newsroom article id; placeholders are 'start-sit' / 'p0'..'p2'
  const [stored, setStored] = useState<any>(null);
  const [weekArticles, setWeekArticles] = useState<any>(null);
  const [dashboardData, setDashboardData] = useState<any>(null);

  useEffect(() => {
    if (!isStored) return;
    fetch(`${API_BASE}/api/news/article/${slug}`)
      .then((res) => res.json())
      .then((data) => { if (!data.error) setStored(data); })
      .catch(() => {});
  }, [slug, isStored]);

  useEffect(() => {
    const week = stored?.week ?? systemStatus.week;
    fetch(`${API_BASE}/api/fantasy/articles?season=${systemStatus.season}&week=${week}`)
      .then((res) => res.json())
      .then(setWeekArticles)
      .catch(() => {});
  }, [stored?.week, systemStatus.season, systemStatus.week]);

  useEffect(() => {
    if (isStored) return;
    fetch(`${API_BASE}/api/fantasy/dashboard?season=${systemStatus.season}&week=${systemStatus.week}`)
      .then((res) => res.json())
      .then(setDashboardData)
      .catch(() => {});
  }, [isStored, systemStatus.season, systemStatus.week]);

  const siblings: any[] = [...(weekArticles?.front_page || []), ...(weekArticles?.more_analysis || [])];
  const card = siblings.find((a) => String(a.id) === slug);

  // Placeholder articles (no stored slate yet) keep the old generic copy.
  const isHero = slug === "start-sit";
  const placeholderIndex = Number(slug.replace(/^p/, "")) || 0;
  const placeholder = isHero ? { ...(dashboardData?.hero || {}) } : { ...(fantasyArticles[placeholderIndex] || fantasyArticles[0]), ...(dashboardData?.articles?.[placeholderIndex] || {}) };

  const label = card?.label || (stored?.article_type ? stored.article_type.replace(/^FANTASY_/, "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c: string) => c.toUpperCase()) : placeholder.label) || "Fantasy Analysis";
  const title = stored?.headline || placeholder.title || "";
  const excerpt = stored?.excerpt || placeholder.excerpt || "";
  const author = card?.author || stored?.author?.display_name || placeholder.author || "Mu Fantasy Desk";
  const readTime = card?.time ? `${card.time} read` : placeholder.time || "";
  const week = stored?.week ?? systemStatus.week;
  const others = siblings.filter((a) => String(a.id) !== slug).slice(0, 5);

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="news" />
      <main className="fantasy-article-page">
        <div className="fantasy-article-crumb"><a href="#fantasy">News &amp; Articles</a><span>/</span><a href="#fantasy-all">All analysis</a><span>/</span><b>{label}</b><span className="fantasy-article-week">SEASON {systemStatus.season} · WEEK {week}</span></div>
        <article className="fantasy-article-panel">
          <header className="fantasy-article-heading">
            <div className="fantasy-article-kicker"><span>{label}</span>{card?.updated && <b>{fantasyDate(card.updated)}</b>}</div>
            <h1>{title}</h1>
            {excerpt && <p>{excerpt}</p>}
            <div className="fantasy-article-byline"><span>{fantasyInitials(author)}</span><div><strong>By {author}</strong><small>MU FANTASY ANALYTICS{readTime ? ` · ${readTime.toUpperCase()}` : ""}</small></div><a href="#fantasy-rankings">Open rankings →</a></div>
          </header>
          <div className="fantasy-article-content">
            <div className="fantasy-article-copy">
              {isStored ? (
                stored ? <FantasyMarkdown content={stored.content} /> : <p>Loading article…</p>
              ) : (
                <>
                  <p className="fantasy-article-lead">{excerpt || title}</p>
                  <p>This week's fantasy analysis will appear here once the Mu Fantasy desk publishes its slate.</p>
                </>
              )}
              <a className="fantasy-article-rankings-link" href="#fantasy-rankings">Explore this week's rankings <span>→</span></a>
            </div>
            <aside className="fantasy-article-rail">
              <span>WEEK {week} · PPR</span>
              <strong>More from the fantasy desk</strong>
              {others.length ? (
                others.map((a) => <a className="fantasy-article-rail-link" href={`#article-fantasy-${a.id}`} key={a.id}><small>{a.label}</small>{a.title}</a>)
              ) : (
                <p>Use role, matchup and projection together. No single number tells the whole story.</p>
              )}
              <a href="#fantasy-all">All analysis this week →</a>
            </aside>
          </div>
        </article>
      </main>
    </div>
  );
}

function FantasyAllPage() {
  const systemStatus = useContext(SystemContext);
  const [requestedWeek, setRequestedWeek] = useState<number>(systemStatus.week);
  const [data, setData] = useState<any>(null);

  useEffect(() => { setRequestedWeek(systemStatus.week); }, [systemStatus.week]);
  useEffect(() => {
    fetch(`${API_BASE}/api/fantasy/articles?season=${systemStatus.season}&week=${requestedWeek}`)
      .then((res) => res.json())
      .then(setData)
      .catch(() => {});
  }, [systemStatus.season, requestedWeek]);

  const sections: [string, any[]][] = [["Front page", data?.front_page || []], ["More analysis", data?.more_analysis || []]];
  const weeks: number[] = data?.available_weeks || [];

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="news" />
      <main className="fantasy-article-page">
        <div className="fantasy-article-crumb"><a href="#fantasy">News &amp; Articles</a><span>/</span><b>All analysis</b><span className="fantasy-article-week">SEASON {systemStatus.season}{data?.week ? ` · WEEK ${data.week}` : ""}</span></div>
        <section className="fantasy-all">
          <div className="fantasy-section-heading">
            <div><span>THE FULL DESK</span><h1>All fantasy analysis</h1></div>
            <p>Every piece from the Mu Fantasy desk for the week, front page first.</p>
          </div>
          {weeks.length > 1 && (
            <div className="fantasy-all-weeks" role="tablist" aria-label="Fantasy week">
              {weeks.map((w) => (
                <button key={w} className={w === data?.week ? "active" : ""} onClick={() => setRequestedWeek(w)}>W{w}</button>
              ))}
            </div>
          )}
          {!data ? (
            <p className="fantasy-all-empty">Loading…</p>
          ) : !data.week ? (
            <p className="fantasy-all-empty">The fantasy desk hasn't published a slate yet this season.</p>
          ) : (
            sections.filter(([, items]) => items.length).map(([name, items]) => (
              <div className="fantasy-all-section" key={name}>
                <div className="fantasy-subhead"><div><span>{String(items.length).padStart(2, "0")}</span><h2>{name}</h2></div></div>
                <div className="fantasy-all-list">
                  {items.map((a) => (
                    <a className="fantasy-all-item" href={`#article-fantasy-${a.id}`} key={a.id}>
                      <span>{a.label}</span>
                      <h3>{a.title}</h3>
                      {a.excerpt && <p>{a.excerpt}</p>}
                      <small>BY {String(a.author || "").toUpperCase()} <b>•</b> {String(a.time || "").toUpperCase()} READ</small>
                    </a>
                  ))}
                </div>
              </div>
            ))
          )}
        </section>
      </main>
    </div>
  );
}

function SocialIcon({ name }: { name: SocialIconName }) {
  const paths: Record<SocialIconName, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    bookmark: <path d="M6 3h12v18l-6-4-6 4Z" />,
    desk: <><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h5" /></>,
    teams: <><circle cx="9" cy="9" r="3" /><circle cx="17" cy="8" r="2" /><path d="M3 20c0-4 2-7 6-7s6 3 6 7M15 13c3 0 5 2 5 5" /></>,
    reply: <path d="M20 17a8 8 0 0 0-13-6l-3 3m0 0 4 3m-4-3h10" />,
    repost: <><path d="m7 7 3-3 3 3M10 4v12" /><path d="m17 17-3 3-3-3M14 20V8" /></>,
    like: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z" />,
    chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></>,
    more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

// ---------------------------------------------------------------------------------------------------------
// Agent Wire (docs/SOCIAL_MATRIX.md; data from /api/social/*, served by server/social_api.py)
// ---------------------------------------------------------------------------------------------------------
const SOCIAL_API = `${API_BASE}/api/social`;

type SocialAuthor = {
  agent_id: string;
  handle: string;
  display_name: string;
  archetype: string;
  label: string;
  team_abbr: string | null;
  team_color: string | null;
  followers: number;
};

type SocialPostData = {
  id: number;
  week: number;
  kind: string;
  kind_label: string;
  text: string;
  posted_at: string | null;
  likes: number;
  reposts: number;
  replies: number;
  views: number;
  hashtags: string[];
  team_abbr: string | null;
  local: boolean;
  author: SocialAuthor;
  reply_to: { id: number; handle: string; display_name: string } | null;
  article: { id: number; headline: string; excerpt: string | null; article_type: string | null } | null;
};

type SocialFeedData = {
  season: number;
  week: number | null;
  weeks: number[];
  as_of: string | null;
  posts: SocialPostData[];
  total?: number;
  next_offset: number | null;
};

type SocialTrend = { id: string; rank: number; kind: string; key: string; title: string; category: string; description: string; volume: number };

function useSocialData<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(url));
  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json) => { if (live) setData(json); })
      .catch((err) => { if (live) setError(String(err?.message || err)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [url]);
  return { data, error, loading, setData };
}

const formatSocialCount = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
};

const SOCIAL_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Posts carry simulated in-world times (no time zone). The feed's "now" is its newest post (as_of).
const socialTime = (iso: string | null | undefined) => (iso ? new Date(iso) : null);

const socialRelativeTime = (iso: string | null, asOf?: string | null) => {
  const t = socialTime(iso);
  if (!t) return "";
  const now = socialTime(asOf);
  if (now) {
    const mins = (now.getTime() - t.getTime()) / 60000;
    if (mins >= 0 && mins < 60) return `${Math.max(1, Math.round(mins))}m`;
    if (mins >= 0 && mins < 24 * 60) return `${Math.round(mins / 60)}h`;
  }
  return `${SOCIAL_MONTHS[t.getMonth()]} ${t.getDate()}`;
};

const socialFullTime = (iso: string | null) => {
  const t = socialTime(iso);
  if (!t) return "";
  const h = t.getHours();
  const clock = `${h % 12 || 12}:${String(t.getMinutes()).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  return `${clock} · ${SOCIAL_MONTHS[t.getMonth()]} ${t.getDate()}, ${t.getFullYear()}`;
};

const socialInitials = (name: string) => {
  const words = (name || "").replace(/[^\p{L}\p{N}\s'-]/gu, "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
};

// Avatars are parked (docs/SOCIAL_MATRIX.md): initials on the team colour, or a colour per kind of account.
const SOCIAL_ACCOUNT_COLORS: Record<string, string> = {
  SCHEFTER_TWEET: "#b3122e", RAPOPORT: "#b3122e", NFL_MEMES: "#6d3fd1", DRAFT_SICKO: "#0e7490",
  FANTASY_GURU: "#15803d", BERRY: "#15803d", YATES: "#15803d", BANDWAGON_FAN: "#b45309",
};

const socialAvatarColor = (author: { archetype?: string; team_color?: string | null }) =>
  author.team_color || SOCIAL_ACCOUNT_COLORS[author.archetype || ""] || "#334155";

function SocialAvatar({ author, size = 46 }: { author: { display_name: string; archetype?: string; team_color?: string | null }; size?: number }) {
  return (
    <span
      className="social-avatar"
      style={{ width: size, height: size, background: socialAvatarColor(author), fontSize: Math.round(size * 0.36) }}
      aria-hidden="true"
    >
      {socialInitials(author.display_name)}
    </span>
  );
}

const socialBadgeClass = (archetype: string) =>
  archetype === "LOCAL_BEAT_REPORTER" ? "beat"
    : archetype === "DIE_HARD_FAN" ? "homer"
    : archetype === "BANDWAGON_FAN" ? "bandwagon"
    : archetype === "FORMER_PLAYER" ? "legend"
    : archetype === "NFL_MEMES" || archetype === "SKIP_TWEET" || archetype === "STEPHEN_A" ? "hot"
    : "analyst";

const socialHandlePath = (handle: string) => (handle || "").replace(/^@/, "");

const socialArticleHref = (article: { id: number; article_type: string | null }) =>
  (article.article_type || "").startsWith("FANTASY_") ? `#article-fantasy-${article.id}` : `#article-${article.id}`;

const socialTrendHref = (kind: string, key: string) => `#social-trend-${kind}-${key}`;

function SocialText({ text }: { text: string }) {
  const parts = (text || "").split(/(#\w+|@\w+)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (/^#\w+$/.test(part)) {
          return <a className="social-link" href={socialTrendHref("HASHTAG", part.slice(1).toLowerCase())} key={i}>{part}</a>;
        }
        if (/^@\w+$/.test(part)) {
          return <a className="social-link" href={`#social-profile-${part.slice(1)}`} key={i}>{part}</a>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function SocialShareCard({ article }: { article: NonNullable<SocialPostData["article"]> }) {
  return (
    <a className="social-share-card" href={socialArticleHref(article)}>
      <span>{(article.article_type || "").startsWith("FANTASY_") ? "MU FANTASY" : "GRIDIRON NEWSROOM"}</span>
      <strong>{article.headline}</strong>
      {article.excerpt && <small>{article.excerpt}</small>}
    </a>
  );
}

function SocialPost({ post, asOf }: { post: SocialPostData; asOf?: string | null }) {
  const profileHref = `#social-profile-${socialHandlePath(post.author.handle)}`;
  const openPost = () => {
    window.location.hash = `#social-post-${post.id}`;
  };

  return (
    <article
      className="social-post social-post-link"
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("button, a")) openPost();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === event.currentTarget) openPost();
      }}
      role="link"
      tabIndex={0}
      aria-label={`Open post by ${post.author.display_name}`}
    >
      <a className="social-post-avatar-link" href={profileHref} aria-label={`View ${post.author.display_name}'s profile`}>
        <SocialAvatar author={post.author} />
      </a>
      <div className="social-post-body">
        <div className="social-post-meta">
          <a className="social-post-author" href={profileHref}>
            <strong>{post.author.display_name}</strong>
            <span>{post.author.handle}</span>
          </a>
          <small>· {socialRelativeTime(post.posted_at, asOf)}</small>
          {post.author.team_abbr && <img src={logo(post.author.team_abbr)} alt={post.author.team_abbr} />}
          <b className={socialBadgeClass(post.author.archetype)}>{post.author.label}</b>
        </div>
        {post.reply_to && (
          <div className="social-reply-to">
            Replying to <a href={`#social-profile-${socialHandlePath(post.reply_to.handle)}`}>{post.reply_to.handle}</a>
          </div>
        )}
        <p><SocialText text={post.text} /></p>
        {post.article && <SocialShareCard article={post.article} />}
        <div className="social-post-actions">
          <button aria-label={`${post.replies} replies`}><SocialIcon name="reply" /><span>{formatSocialCount(post.replies)}</span></button>
          <button aria-label={`${post.reposts} reposts`}><SocialIcon name="repost" /><span>{formatSocialCount(post.reposts)}</span></button>
          <button aria-label={`${post.likes} likes`}><SocialIcon name="like" /><span>{formatSocialCount(post.likes)}</span></button>
        </div>
      </div>
    </article>
  );
}

function SocialStatus({ loading, error, empty, emptyTitle, emptyText }: {
  loading: boolean; error: string | null; empty: boolean; emptyTitle: string; emptyText: string;
}) {
  if (loading) return <div className="social-empty"><span>Loading Agent Wire…</span></div>;
  if (error) {
    return (
      <div className="social-empty">
        <strong>Agent Wire couldn't load</strong>
        <span>Check that the API is running on localhost:8000, then refresh. ({error})</span>
      </div>
    );
  }
  if (empty) return <div className="social-empty"><strong>{emptyTitle}</strong><span>{emptyText}</span></div>;
  return null;
}

/** A paged list of posts from one /api/social feed URL (without offset). */
function SocialPostList({ url, emptyTitle, emptyText, onLoaded }: {
  url: string; emptyTitle: string; emptyText: string; onLoaded?: (data: SocialFeedData) => void;
}) {
  const { data, error, loading } = useSocialData<SocialFeedData>(url);
  const [extra, setExtra] = useState<SocialPostData[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setExtra([]);
    setNextOffset(data?.next_offset ?? null);
    if (data && onLoaded) onLoaded(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const loadMore = () => {
    if (nextOffset == null) return;
    setLoadingMore(true);
    fetch(`${url}${url.includes("?") ? "&" : "?"}offset=${nextOffset}`)
      .then((res) => res.json())
      .then((more: SocialFeedData) => {
        setExtra((prev) => [...prev, ...(more.posts || [])]);
        setNextOffset(more.next_offset ?? null);
      })
      .catch(() => setNextOffset(null))
      .finally(() => setLoadingMore(false));
  };

  const posts = [...(data?.posts || []), ...extra];
  return (
    <section className="social-timeline">
      <SocialStatus loading={loading} error={error} empty={!loading && !error && posts.length === 0}
        emptyTitle={emptyTitle} emptyText={emptyText} />
      {posts.map((post) => <SocialPost post={post} asOf={data?.as_of} key={post.id} />)}
      {nextOffset != null && (
        <button className="social-load-more" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Show more posts"}
        </button>
      )}
    </section>
  );
}

function SocialNavigation({ active }: { active: "home" | "explore" | "teams" }) {
  return (
    <aside className="social-left">
      <div className="social-left-inner">
        <a className="social-logo" href="#social" aria-label="Agent Wire home"><span>AW</span><div><strong>AGENT WIRE</strong><small>GRIDIRON SOCIAL</small></div></a>
        <nav className="social-primary-nav" aria-label="Agent Wire navigation">
          <a className={active === "home" ? "active" : ""} href="#social"><SocialIcon name="home" /><span>Home</span></a>
          <a className={active === "explore" ? "active" : ""} href="#social-explore"><SocialIcon name="search" /><span>Explore</span></a>
          <a className={active === "teams" ? "active" : ""} href="#social-teams"><SocialIcon name="teams" /><span>Team Hubs</span></a>
          <a href="#"><SocialIcon name="desk" /><span>NFL</span></a>
        </nav>
      </div>
    </aside>
  );
}

function SocialTrendsWidget({ limit = 6 }: { limit?: number }) {
  const { data } = useSocialData<{ trends: SocialTrend[] }>(`${SOCIAL_API}/trends?limit=${limit}`);
  const trends = data?.trends || [];
  if (!trends.length) return null;
  return (
    <section className="social-widget social-trends">
      <div className="social-widget-head"><div><h2>What's happening</h2></div></div>
      {trends.map((trend) => (
        <a href={socialTrendHref(trend.kind, trend.key)} key={trend.id}>
          <span>{String(trend.rank).padStart(2, "0")}</span>
          <div><small>{trend.category} · Trending</small><strong>{trend.title}</strong><b>{formatSocialCount(trend.volume)} posts</b></div>
          <SocialIcon name="more" />
        </a>
      ))}
      <a className="social-show-more" href="#social-explore">Show more</a>
    </section>
  );
}

function SocialWhoToFollow() {
  const { data } = useSocialData<{ accounts: { handle: string; display_name: string; label: string; archetype?: string; team_abbr: string | null }[] }>(
    `${SOCIAL_API}/suggestions?limit=3`,
  );
  const accounts = data?.accounts || [];
  if (!accounts.length) return null;
  return (
    <section className="social-widget social-follows">
      <div className="social-widget-head"><div><span>EXPAND YOUR FEED</span><h2>Who to follow</h2></div></div>
      {accounts.map((person) => (
        <a className="social-follow-row" href={`#social-profile-${socialHandlePath(person.handle)}`} key={person.handle}>
          <SocialAvatar author={person} size={36} />
          <span><strong>{person.display_name}</strong><small>{person.handle} · {person.label}</small></span>
        </a>
      ))}
    </section>
  );
}

function SocialRightRail({ trends = true }: { trends?: boolean }) {
  const systemStatus = useContext(SystemContext);
  return (
    <aside className="social-right">
      <div className="social-right-inner">
        {trends && <SocialTrendsWidget />}
        <SocialNflWidget />
        <SocialWhoToFollow />
        <div className="social-legal"><a href="#">Terms</a><a href="#">Privacy</a><a href="#">About</a><span>© {systemStatus.season} AGENT WIRE</span></div>
      </div>
    </aside>
  );
}

function SocialFeedPage() {
  const [tab, setTab] = useState<"foryou" | "latest">("foryou");
  const [meta, setMeta] = useState<SocialFeedData | null>(null);
  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed">
        <header className="social-feed-header">
          <div className="social-feed-tabs" role="tablist" aria-label="Feed">
            <button role="tab" aria-selected={tab === "foryou"} className={tab === "foryou" ? "active" : ""} onClick={() => setTab("foryou")}>
              <strong>For You</strong><small>MOST TALKED ABOUT</small>
            </button>
            <button role="tab" aria-selected={tab === "latest"} className={tab === "latest" ? "active" : ""} onClick={() => setTab("latest")}>
              <strong>Latest</strong><small>NEWEST FIRST</small>
            </button>
          </div>
        </header>
        {meta?.week != null && (
          <div className="social-feed-context">
            <span><i />WEEK {meta.week}</span>
            <p>{meta.total ?? meta.posts.length} posts this week</p>
            <b>{meta.as_of ? socialFullTime(meta.as_of).split(" · ")[1] : ""}</b>
          </div>
        )}
        <SocialPostList
          key={tab}
          url={`${SOCIAL_API}/feed?tab=${tab}&limit=30`}
          emptyTitle="No posts yet"
          emptyText="Agent Wire posts are written by the weekly newsroom run."
          onLoaded={setMeta}
        />
      </main>
      <SocialRightRail />
    </div>
  );
}

function SocialPostDetailPage({ postId }: { postId: number }) {
  const { data, error, loading } = useSocialData<{ post?: SocialPostData; parent?: SocialPostData | null; replies?: SocialPostData[]; error?: string }>(
    `${SOCIAL_API}/post/${postId}`,
  );
  const post = data?.post;
  const replies = data?.replies || [];
  const asOf = [post?.posted_at, ...replies.map((r) => r.posted_at)].filter(Boolean).sort().pop() || null;
  const profileHref = post ? `#social-profile-${socialHandlePath(post.author.handle)}` : "#social";

  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed social-post-detail">
        <header className="social-post-detail-header">
          <a href="#social" aria-label="Back to Agent Wire">←</a>
          <h1>Post</h1>
        </header>
        <SocialStatus loading={loading} error={error || data?.error || null} empty={false} emptyTitle="" emptyText="" />
        {data?.parent && <div className="social-thread-parent"><SocialPost post={data.parent} asOf={asOf} /></div>}
        {post && (
          <article className="social-post-detail-card">
            <header>
              <a className="social-post-detail-avatar" href={profileHref} aria-label={`View ${post.author.display_name}'s profile`}>
                <SocialAvatar author={post.author} size={48} />
              </a>
              <a className="social-post-detail-author" href={profileHref}>
                <strong>{post.author.display_name}</strong>
                <span>{post.author.handle} · {post.author.label}</span>
              </a>
              {post.author.team_abbr ? <img className="social-detail-team" src={logo(post.author.team_abbr)} alt={post.author.team_abbr} /> : <span />}
            </header>
            {post.reply_to && (
              <div className="social-reply-to">
                Replying to <a href={`#social-profile-${socialHandlePath(post.reply_to.handle)}`}>{post.reply_to.handle}</a>
              </div>
            )}
            <p><SocialText text={post.text} /></p>
            {post.article && <SocialShareCard article={post.article} />}
            <time>{socialFullTime(post.posted_at)} · {formatSocialCount(post.views)} views</time>
            <div className="social-post-detail-stats">
              <span><strong>{formatSocialCount(post.reposts)}</strong> Reposts</span>
              <span><strong>{formatSocialCount(post.likes)}</strong> Likes</span>
              <span><strong>{formatSocialCount(post.replies)}</strong> Replies</span>
            </div>
            <div className="social-post-detail-actions">
              <button aria-label="Reply"><SocialIcon name="reply" /></button>
              <button aria-label="Repost"><SocialIcon name="repost" /></button>
              <button aria-label="Like"><SocialIcon name="like" /></button>
            </div>
          </article>
        )}
        {post && (
          <section className="social-replies" aria-label="Replies">
            <h2>Replies</h2>
            {replies.length === 0 && <div className="social-empty social-empty-small"><span>No replies on Agent Wire yet.</span></div>}
            {replies.map((reply) => <SocialPost post={reply} asOf={asOf} key={reply.id} />)}
          </section>
        )}
      </main>
      <SocialRightRail />
    </div>
  );
}

function SocialProfilePage({ handle }: { handle: string }) {
  const { data, error, loading } = useSocialData<{
    account?: { handle: string; display_name: string; bio: string; label: string; archetype: string; team_abbr: string | null; team_color: string | null; followers: number; following: number };
    post_count?: number; posts?: SocialPostData[]; error?: string;
  }>(`${SOCIAL_API}/profile/${encodeURIComponent(handle)}`);
  const account = data?.account;
  const posts = data?.posts || [];
  const asOf = posts[0]?.posted_at || null;

  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed social-profile">
        <header className="social-profile-nav">
          <a href="#social" aria-label="Back to Agent Wire">←</a>
          <div><h1>{account?.display_name || `@${handle}`}</h1><span>{data?.post_count ?? 0} {data?.post_count === 1 ? "post" : "posts"}</span></div>
        </header>
        <SocialStatus loading={loading} error={error || data?.error || null} empty={false} emptyTitle="" emptyText="" />
        {account && (
          <section className="social-profile-hero">
            <div className="social-profile-banner" style={account.team_color ? { background: `linear-gradient(135deg, ${account.team_color}, #0c0f13 85%)` } : undefined} />
            <div className="social-profile-summary">
              <div className="social-profile-avatar"><SocialAvatar author={account} size={96} /></div>
              <h2>{account.display_name}</h2>
              <span>{account.handle} · {account.label}</span>
              {account.bio && <p>{account.bio}</p>}
              <div>
                {account.team_abbr && <a className="social-profile-team" href={`#social-team-${account.team_abbr.toLowerCase()}`}><img src={logo(account.team_abbr)} alt="" />Team Hub</a>}
                <span><strong>{formatSocialCount(account.following)}</strong> Following</span>
                <span><strong>{formatSocialCount(account.followers)}</strong> Followers</span>
              </div>
            </div>
            <nav aria-label="Profile content"><span>Posts</span></nav>
          </section>
        )}
        {account && (
          <section className="social-timeline" aria-label={`Posts by ${account.display_name}`}>
            {posts.length === 0 && <div className="social-empty"><strong>No posts yet</strong><span>This account hasn't posted this season.</span></div>}
            {posts.map((post) => <SocialPost post={post} asOf={asOf} key={post.id} />)}
          </section>
        )}
      </main>
      <SocialRightRail />
    </div>
  );
}

function SocialTeamHubsPage() {
  return (
    <div className="social-shell">
      <SocialNavigation active="teams" />
      <main className="social-feed social-team-hubs">
        <header className="social-team-hubs-header">
          <span>FOLLOW THE LEAGUE</span>
          <h1>Team Hubs</h1>
          <p>Choose a team to open its Agent Wire feed: its beat reporters, its fans and what the league is saying about it.</p>
        </header>
        <section className="social-team-grid" aria-label="NFL team hubs">
          {teams.map((team) => (
            <a className="social-team-card" href={`#social-team-${team.abbr}`} key={team.abbr}>
              <img src={logo(team.abbr)} alt="" />
              <span>
                <strong>{team.name}</strong>
              </span>
              <i aria-hidden="true">→</i>
            </a>
          ))}
        </section>
      </main>
      <SocialRightRail />
    </div>
  );
}

function SocialTeamFeedPage({ team }: { team: Team }) {
  const [meta, setMeta] = useState<(SocialFeedData & { team?: { record: string | null; division: string | null }; wakeup_started?: boolean }) | null>(null);
  return (
    <div className="social-shell">
      <SocialNavigation active="teams" />
      <main className="social-feed">
        <header className="social-team-feed-header">
          <a className="social-team-feed-back" href="#social-teams" aria-label="Back to Team Hubs">←</a>
          <a className="social-team-feed-logo" href={`#team-${team.abbr}`} aria-label={`View ${team.name} team page`}>
            <img src={logo(team.abbr)} alt="" />
          </a>
          <div>
            <h1><a href={`#team-${team.abbr}`}>{team.name}</a></h1>
            {meta?.team?.record && <span className="social-team-feed-sub">{meta.team.record} · {meta.team.division}</span>}
          </div>
        </header>
        {meta?.wakeup_started && (
          <div className="social-wakeup-note">The {team.name.split(" ").pop()} beat and fans are writing this week's posts. Refresh in a minute or two to see them.</div>
        )}
        <SocialPostList
          url={`${SOCIAL_API}/team/${team.abbr.toUpperCase()}?tab=latest&limit=40`}
          emptyTitle="No posts about this team yet"
          emptyText="Posts show up here after the weekly run or once this hub's wakeup finishes."
          onLoaded={(d) => setMeta(d as typeof meta)}
        />
      </main>
      <SocialRightRail />
    </div>
  );
}

function SocialTrendPage({ kind, trendKey }: { kind: string; trendKey: string }) {
  const { data } = useSocialData<{ trends: SocialTrend[] }>(`${SOCIAL_API}/trends?limit=50`);
  const trend = data?.trends.find((t) => t.kind === kind && t.key.toLowerCase() === trendKey.toLowerCase());
  const title = trend?.title || (kind === "HASHTAG" ? `#${trendKey}` : "Trending");
  return (
    <div className="social-shell">
      <SocialNavigation active="explore" />
      <main className="social-feed social-explore">
        <div className="social-results-head">
          <a href="#social-explore" aria-label="Back to Explore">←</a>
          <div>
            <h1>{title}</h1>
            <span>{trend ? `${trend.category} · ${formatSocialCount(trend.volume)} posts` : "Posts this week"}</span>
          </div>
        </div>
        <SocialPostList
          url={`${SOCIAL_API}/feed?tab=foryou&limit=30&trend=${encodeURIComponent(`${kind}:${trendKey}`)}`}
          emptyTitle="No posts found"
          emptyText="Nothing on Agent Wire matches this trend this week."
        />
      </main>
      <SocialRightRail trends={false} />
    </div>
  );
}

function SocialExplorePage() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(id);
  }, [query]);
  const trendsData = useSocialData<{ trends: SocialTrend[] }>(`${SOCIAL_API}/trends?limit=20`);
  const search = useSocialData<{ posts: SocialPostData[]; accounts: { handle: string; display_name: string; label: string; team_abbr: string | null; followers: number }[] }>(
    debounced.length >= 2 ? `${SOCIAL_API}/search?q=${encodeURIComponent(debounced)}` : null,
  );
  const searching = debounced.length >= 2;
  const results = search.data?.posts || [];
  const accounts = search.data?.accounts || [];
  const asOf = results.map((p) => p.posted_at).filter(Boolean).sort().pop() || null;
  const trends = trendsData.data?.trends || [];

  return (
    <div className="social-shell">
      <SocialNavigation active="explore" />
      <main className="social-feed social-explore">
        <header className="social-explore-header">
          <label className="social-explore-search">
            <SocialIcon name="search" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search Agent Wire"
              aria-label="Search Agent Wire posts and accounts"
            />
            {query && <button onClick={() => setQuery("")} aria-label="Clear search">×</button>}
          </label>
        </header>

        {searching ? (
          <>
            <div className="social-results-head">
              <button onClick={() => setQuery("")} aria-label="Back to Explore">←</button>
              <div>
                <h1>Results for “{debounced}”</h1>
                <span>{search.loading ? "Searching…" : `${results.length} ${results.length === 1 ? "post" : "posts"} · ${accounts.length} ${accounts.length === 1 ? "account" : "accounts"}`}</span>
              </div>
            </div>
            {accounts.length > 0 && (
              <section className="social-account-results" aria-label="Accounts">
                {accounts.map((a) => (
                  <a className="social-follow-row" href={`#social-profile-${socialHandlePath(a.handle)}`} key={a.handle}>
                    <SocialAvatar author={{ display_name: a.display_name }} size={36} />
                    <span><strong>{a.display_name}</strong><small>{a.handle} · {a.label} · {formatSocialCount(a.followers)} followers</small></span>
                  </a>
                ))}
              </section>
            )}
            <section className="social-timeline" aria-label="Search results">
              <SocialStatus loading={search.loading} error={search.error} empty={!search.loading && !search.error && results.length === 0 && accounts.length === 0}
                emptyTitle="No posts found" emptyText="Try a team, a player, an account or a hashtag." />
              {results.map((post) => <SocialPost post={post} asOf={asOf} key={post.id} />)}
            </section>
          </>
        ) : (
          <section className="social-explore-topics">
            <div className="social-explore-title"><span>TRENDING ACROSS THE LEAGUE</span><h1>What's happening</h1></div>
            <SocialStatus loading={trendsData.loading} error={trendsData.error} empty={!trendsData.loading && !trendsData.error && trends.length === 0}
              emptyTitle="Nothing trending yet" emptyText="Trends appear after the weekly run writes its posts." />
            {trends.map((item) => (
              <button onClick={() => { window.location.hash = socialTrendHref(item.kind, item.key); }} key={item.id}>
                <span>{item.category} · Trending</span>
                <strong>{item.title}</strong>
                <small>{formatSocialCount(item.volume)} posts</small>
                <b>{String(item.rank).padStart(2, "0")}</b>
              </button>
            ))}
          </section>
        )}
      </main>
      <SocialRightRail trends={false} />
    </div>
  );
}

function SocialNflWidget() {
  const systemStatus = useContext(SystemContext);
  const [games, setGames] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/schedule?season=${systemStatus.season}&week=${systemStatus.week}`)
      .then((res) => res.json())
      .then((data: any[]) => {
        if (!Array.isArray(data)) return;
        // Rank games by Excitement Index (matching the fantasy dashboard)
        const getExcitement = (g: any) => Number(g.excitement_index ?? g.watchability ?? 0);
        const sorted = [...data].sort((a, b) => getExcitement(b) - getExcitement(a));
        setGames(sorted.slice(0, 3));
      })
      .catch(() => setGames([]));
  }, [systemStatus.season, systemStatus.week]);

  const dayMap: Record<string, string> = {
    Sun: "Sunday",
    Thu: "Thursday",
    Mon: "Monday",
    Sat: "Saturday",
    Fri: "Friday",
  };

  const formatGameTime = (game: any) => {
    if (game.status === "COMPLETED") {
      return {
        top: "FINAL",
        bottom: `${game.away_score}–${game.home_score}`,
      };
    }
    const parts = (game.game_time || "Sun 1:00 PM").split(" ");
    const day = dayMap[parts[0]] || parts[0] || "Sunday";
    const time = parts.slice(1).join(" ") || "1:00 PM";
    return { top: day, bottom: time };
  };

  return (
    <section className="social-widget social-nfl-widget">
      <header>
        <div>
          <img
            className="social-nfl-shield-img"
            src="https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png"
            alt="NFL"
            style={{ width: "24px", height: "30px", objectFit: "contain", display: "block" }}
          />
          <h2>NFL</h2>
        </div>
        <small>WEEK {systemStatus.week}</small>
      </header>
      <div className="social-nfl-games">
        {games.map((game) => {
          const timing = formatGameTime(game);
          const awayAbbr = (game.away_abbr || "").toLowerCase();
          const homeAbbr = (game.home_abbr || "").toLowerCase();
          const awayRecord = `${game.away_wins ?? 0}–${game.away_losses ?? 0}`;
          const homeRecord = `${game.home_wins ?? 0}–${game.home_losses ?? 0}`;

          return (
            <a href={`#game-${game.id}`} key={game.id}>
              <div>
                <span className="social-nfl-team">
                  <img src={logo(awayAbbr)} alt={game.away_name} />
                  <strong>{game.away_name}</strong>
                  <small>{awayRecord}</small>
                </span>
                <span className="social-nfl-team">
                  <img src={logo(homeAbbr)} alt={game.home_name} />
                  <strong>{game.home_name}</strong>
                  <small>{homeRecord}</small>
                </span>
              </div>
              <span className="social-nfl-time">
                <strong>{timing.top}</strong>
                <small>{timing.bottom}</small>
              </span>
            </a>
          );
        })}
      </div>
      <a className="social-nfl-more" href="#schedule">Show more</a>
    </section>
  );
}

function Chevron({ direction = "down" }: { direction?: "down" | "up" | "left" | "right" }) {
  const rotation = direction === "left" ? "rotate(90 12 12)" : direction === "right" ? "rotate(-90 12 12)" : direction === "up" ? "rotate(180 12 12)" : "";
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="m7 9.5 5 5 5-5" transform={rotation} />
    </svg>
  );
}

function ScoreTicker() {
  const { season, week } = useContext(SystemContext);
  const [games, setGames] = useState<any[]>([]);
  const prevWeek = week > 1 ? week - 1 : 1;
  
  useEffect(() => {
    fetch(`${API_BASE}/api/schedule?season=${season}&week=${prevWeek}`)
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data)) {
          setGames([...data, ...data]); // duplicate for infinite scroll
        }
      });
  }, [season, prevWeek]);

  return (
    <div className="ticker" aria-label="Previous week's results">
      <div className="ticker-label"><span>WEEK {prevWeek}</span><strong>FINAL SCORES</strong></div>
      <div className="ticker-viewport">
        <div className="ticker-track">
          {games.map((game, index) => (
            <a className="ticker-game" href={`#game-${game.id}`} key={`${game.id}-${index}`}>
              <div className="ticker-matchup">
                <span><img src={logo(game.away_abbr.toLowerCase())} alt="" />{game.away_abbr.toUpperCase()} {game.status === 'COMPLETED' && <b>{game.away_score}</b>}</span>
                <i>@</i>
                <span><img src={logo(game.home_abbr.toLowerCase())} alt="" />{game.home_abbr.toUpperCase()} {game.status === 'COMPLETED' && <b>{game.home_score}</b>}</span>
              </div>
              <span className="game-time">{game.status === 'COMPLETED' ? `FINAL · ${game.game_time ? game.game_time.toUpperCase() : ''}` : game.game_time ? game.game_time.toUpperCase() : "TBD"}</span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function MegaMenu({
  week,
  setWeek,
  close,
}: {
  week: number;
  setWeek: (week: number) => void;
  close: () => void;
}) {
  const systemStatus = useContext(SystemContext);
  const weekTabs = week === 1 ? [1, 2, 3] : week >= 23 ? [21, 22, 23] : [week - 1, week, week + 1];
  const [highlightedGames, setHighlightedGames] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/schedule?season=${systemStatus.season}&week=${week}`)
      .then(res => res.json())
      .then(data => {
        if (!data.error) {
          setHighlightedGames(data.slice(0, 3));
        }
      });
  }, [systemStatus.season, week]);

  return (
    <div className="mega-wrap">
      <button className="mega-scrim" aria-label="Close teams menu" onClick={close} />
      <div className="mega-menu">
        <div className="mega-inner">
          <section className="teams-panel">
            <div className="mega-heading">
              <div><span className="eyebrow">League directory</span><h2>All 32 Teams</h2></div>
              <a href="#standings" onClick={close}>View standings <span>↗</span></a>
            </div>
            <div className="team-grid">
              {teams.map((team) => (
                <a className="team-link" href={`#team-${team.abbr}`} onClick={close} key={team.abbr}>
                  <img src={logo(team.abbr)} alt="" />
                  <span>{team.name.replace("Los Angeles ", "LA ").replace("New York ", "NY ")}</span>
                </a>
              ))}
            </div>
          </section>
          <section className="scores-panel">
            <div className="mega-heading score-heading">
              <div><span className="eyebrow">League schedule</span><h2>Week {week}</h2></div>
              <div className="week-arrows">
                <button onClick={() => setWeek(Math.max(1, week - 1))} disabled={week === 1} aria-label="Previous week"><Chevron direction="left" /></button>
                <button onClick={() => setWeek(Math.min(23, week + 1))} disabled={week >= 23} aria-label="Next week"><Chevron direction="right" /></button>
              </div>
            </div>
            <div className="week-tabs">
              {weekTabs.map((item) => (
                <button className={week === item ? "active" : ""} onClick={() => setWeek(item)} key={item}>WK {item}</button>
              ))}
            </div>
            <div className="games-list">
              {highlightedGames.map((game) => {
                const parts = game.game_time ? game.game_time.split(" ") : [];
                const label = parts[0] ? parts[0].toUpperCase() : "GAME";
                const timeStr = parts.slice(1).join(" ");
                return (
                  <a className="schedule-game" href={`#game-${systemStatus.season}-${week}-${game.away_abbr.toLowerCase()}-${game.home_abbr.toLowerCase()}`} onClick={close} key={game.id}>
                    <span className="game-status"><b>{label}</b>{game.status === 'COMPLETED' ? "FINAL" : timeStr}</span>
                    <div><img src={logo(game.away_abbr.toLowerCase())} alt="" /><b>{game.away_abbr.toUpperCase()}</b><strong>{game.status === 'COMPLETED' ? game.away_score : "—"}</strong></div>
                    <div><img src={logo(game.home_abbr.toLowerCase())} alt="" /><b>{game.home_abbr.toUpperCase()}</b><strong>{game.status === 'COMPLETED' ? game.home_score : "—"}</strong></div>
                  </a>
                );
              })}
            </div>
            <a className="full-schedule" href="#schedule" onClick={close}>Full league schedule <span>→</span></a>
          </section>
        </div>
      </div>
    </div>
  );
}

function StatsDropdown({ close }: { close: () => void }) {
  return (
    <div className="stats-dropdown-wrap">
      <button className="mega-scrim" aria-label="Close stats menu" onClick={close} />
      <div className="stats-dropdown">
        <div className="stats-dropdown-title"><span className="eyebrow">League Data Center</span><h2>Explore Statistics</h2></div>
        <div className="stats-dropdown-options">
          <a href="#stats-team" onClick={close}>
            <span>01</span>
            <div><strong>Team Stats</strong><small>Compare all 32 clubs across offense, defense and special teams.</small></div>
            <i>→</i>
          </a>
          <a href="#stats-player" onClick={close}>
            <span>02</span>
            <div><strong>Player Stats</strong><small>League leaders, individual production and position rankings.</small></div>
            <i>→</i>
          </a>
        </div>
        <div className="stats-dropdown-group"><span className="eyebrow">History</span></div>
        <div className="stats-dropdown-options">
          <a href="#records" onClick={close}>
            <span>03</span>
            <div><strong>Record Book</strong><small>NFL records, franchise marks and the records set in this league.</small></div>
            <i>→</i>
          </a>
          <a href="#awards" onClick={close}>
            <span>04</span>
            <div><strong>Awards &amp; Champions</strong><small>Super Bowl champions, MVPs and year-end honors, and the All-Pro teams.</small></div>
            <i>→</i>
          </a>
        </div>
      </div>
    </div>
  );
}

type SearchTeamResult = {
  id: number;
  name: string;
  city: string;
  fullName: string;
  abbr: string;
  displayAbbr: string;
  conference: string;
  division: string;
  color: string;
};

type SearchPlayerResult = {
  id: number;
  name: string;
  position: string;
  overall: number | null;
  number: number | null;
  teamAbbr: string;
  teamDisplayAbbr: string;
  teamName: string;
  espn_id?: number | string | null;
  history?: boolean;          // played only in 2026-2034 league history
  lastSeason?: number | null;
};

function GlobalSearchModal({ close }: { close: () => void }) {
  const systemStatus = useContext(SystemContext);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [teamResults, setTeamResults] = useState<SearchTeamResult[]>([]);
  const [playerResults, setPlayerResults] = useState<SearchPlayerResult[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [close]);

  // Search effect
  useEffect(() => {
    const clean = query.trim();
    if (!clean) {
      setTeamResults([]);
      setPlayerResults([]);
      setLoading(false);
      return;
    }

    // Instant local team matches from teams constant
    const cleanLower = clean.toLowerCase();
    const localTeams = teams.filter(
      (t) =>
        t.name.toLowerCase().includes(cleanLower) ||
        t.abbr.toLowerCase() === cleanLower
    );
    if (localTeams.length > 0) {
      setTeamResults(
        localTeams.map((t) => ({
          id: 0,
          name: t.name,
          city: "",
          fullName: t.name,
          abbr: t.abbr,
          displayAbbr: t.abbr.toUpperCase(),
          conference: "",
          division: "",
          color: "#e31837",
        }))
      );
    }

    setLoading(true);
    const timer = setTimeout(() => {
      fetch(`${API_BASE}/api/search?q=${encodeURIComponent(clean)}&season=${systemStatus.season}`)
        .then((res) => res.json())
        .then((data) => {
          setTeamResults(data.teams || []);
          setPlayerResults(data.players || []);
          setLoading(false);
          setSelectedIndex(0);
        })
        .catch(() => {
          setLoading(false);
        });
    }, 150);

    return () => clearTimeout(timer);
  }, [query, systemStatus.season]);

  const totalItems = teamResults.length + playerResults.length;

  const navigateTo = (type: "team" | "player", target: SearchTeamResult | SearchPlayerResult) => {
    if (type === "team") {
      window.location.hash = `#team-${target.abbr.toLowerCase()}`;
    } else {
      window.location.hash = `#player-${target.id}`;
    }
    close();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (totalItems > 0 ? (prev + 1) % totalItems : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (totalItems > 0 ? (prev - 1 + totalItems) % totalItems : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (totalItems === 0) return;
      if (selectedIndex < teamResults.length) {
        navigateTo("team", teamResults[selectedIndex]);
      } else {
        const playerIdx = selectedIndex - teamResults.length;
        if (playerResults[playerIdx]) {
          navigateTo("player", playerResults[playerIdx]);
        }
      }
    }
  };

  return (
    <div className="global-search-modal">
      <div className="global-search-scrim" onClick={close} />
      <div className="global-search-dialog">
        <div className="global-search-input-wrap">
          <svg className="global-search-icon" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="6.5" />
            <path d="m16 16 4 4" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            className="global-search-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search players or teams (e.g. Crawford, Chiefs, WR)..."
            aria-label="Search players or teams"
          />
          {query && (
            <button
              type="button"
              className="global-search-clear"
              onClick={() => { setQuery(""); inputRef.current?.focus(); }}
              aria-label="Clear query"
            >
              ×
            </button>
          )}
          <span className="global-search-shortcut">ESC</span>
        </div>

        <div className="global-search-body">
          {!query.trim() ? (
            <div className="global-search-empty-state">
              <p>Search across all 32 NFL franchises and simulated league players.</p>
              <div className="global-search-suggestions">
                <span>Try searching:</span>
                <button type="button" onClick={() => setQuery("Crawford")}>George Crawford</button>
                <button type="button" onClick={() => setQuery("Panthers")}>Carolina Panthers</button>
                <button type="button" onClick={() => setQuery("Chiefs")}>Kansas City Chiefs</button>
                <button type="button" onClick={() => setQuery("Davis")}>Trey'Mane Davis</button>
              </div>
            </div>
          ) : (
            <div className="global-search-results">
              {loading && teamResults.length === 0 && playerResults.length === 0 && (
                <div className="global-search-loading">Searching league database…</div>
              )}

              {teamResults.length > 0 && (
                <div className="global-search-group">
                  <div className="global-search-group-header">TEAMS ({teamResults.length})</div>
                  {teamResults.map((team, idx) => {
                    const isSelected = selectedIndex === idx;
                    return (
                      <div
                        key={`team-${team.abbr}-${idx}`}
                        className={`global-search-item ${isSelected ? "selected" : ""}`}
                        onMouseEnter={() => setSelectedIndex(idx)}
                        onClick={() => navigateTo("team", team)}
                      >
                        <img src={logo(team.abbr)} alt="" className="global-search-item-logo" />
                        <div className="global-search-item-info">
                          <strong>{team.fullName || team.name}</strong>
                          <small>{team.division ? `${team.division} · ${team.displayAbbr}` : team.displayAbbr}</small>
                        </div>
                        <span className="global-search-item-badge">Team</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {playerResults.length > 0 && (
                <div className="global-search-group">
                  <div className="global-search-group-header">PLAYERS ({playerResults.length})</div>
                  {playerResults.map((player, idx) => {
                    const itemIdx = teamResults.length + idx;
                    const isSelected = selectedIndex === itemIdx;
                    return (
                      <div
                        key={`player-${player.id}`}
                        className={`global-search-item ${isSelected ? "selected" : ""}`}
                        onMouseEnter={() => setSelectedIndex(itemIdx)}
                        onClick={() => navigateTo("player", player)}
                      >
                        <div className="global-search-item-avatar">
                          <img
                            src={getPlayerHeadshot(player)}
                            alt=""
                            onError={(e) => handleHeadshotError(e, player)}
                          />
                        </div>
                        <div className="global-search-item-info">
                          <strong>{player.name}</strong>
                          <small>
                            {player.number ? `#${player.number} · ` : ""}{player.position} · {player.teamDisplayAbbr || player.teamName}
                          </small>
                        </div>
                        <span className="global-search-item-ovr">
                          {player.history ? `History · last ${player.lastSeason}` : `${player.overall} OVR`}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {!loading && teamResults.length === 0 && playerResults.length === 0 && (
                <div className="global-search-no-results">
                  No players or teams found matching <strong>"{query}"</strong>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="global-search-footer">
          <span><kbd>↑</kbd> <kbd>↓</kbd> Navigate</span>
          <span><kbd>↵</kbd> Select</span>
          <span><kbd>ESC</kbd> Close</span>
        </div>
      </div>
    </div>
  );
}

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
      ) : <main>
        {(() => {
          const canAccessOffseason = hasOffseasonArticles || week === 24 || (systemStatus.week && systemStatus.week >= 24);
          const maxNewsWeek = canAccessOffseason ? Math.max(systemStatus.week || 24, 24) : Math.max(systemStatus.week || 19, 19);

          return (
            <div className="edition-bar">
              <span>{week === 24 ? "OFFSEASON EDITION" : "THE NEWSROOM"}</span>
              <span>{systemStatus.season} SEASON</span>
              <div className="edition-week-picker">
                <button
                  className="edition-week-btn"
                  onClick={() => setWeek(Math.max(1, week - 1))}
                  disabled={week <= 1}
                  aria-label="Previous week"
                  title="Previous week"
                >
                  ◀
                </button>
                <select
                  className="edition-week-select"
                  value={week}
                  onChange={(e) => setWeek(Number(e.target.value))}
                  aria-label="Select news week"
                >
                  {Array.from({ length: maxNewsWeek }, (_, i) => i + 1).map((w) => (
                    <option key={w} value={w}>
                      WEEK {w}{w === 19 ? " · WILD CARD" : w === 20 ? " · DIVISIONAL" : w === 21 ? " · CONF CHAMP" : w === 22 ? " · PRO BOWL" : w === 23 ? " · SUPER BOWL" : w === 24 ? " · OFFSEASON" : ""}
                    </option>
                  ))}
                </select>
                <button
                  className="edition-week-btn"
                  onClick={() => setWeek(Math.min(maxNewsWeek, week + 1))}
                  disabled={week >= maxNewsWeek}
                  aria-label="Next week"
                  title="Next week"
                >
                  ▶
                </button>
              </div>
            </div>
          );
        })()}

        <div className="page-grid" style={week === 24 ? { gridTemplateColumns: "1fr" } : undefined}>
          <section className="main-column">
            <div className="section-heading">
              <div><span className="red-rule" /><h1>National Desk</h1></div>
              <p>{week === 24 ? "Offseason reporting, analysis, and editorial package" : `Week ${week} reporting, analysis, and film study`}</p>
            </div>

            {/* HERO STORY */}
            {(() => {
              const hero = newsData?.slotted?.hero;
              const heroId = hero?.id ?? "game-of-week";
              const heroTitle = hero?.headline || (week === 24 ? "The Super Bowl: How the Game Was Won" : "One night. Two contenders. Everything on the line.");
              const heroExcerpt = hero?.excerpt || (week === 24 ? "Inside the pivotal fourth-quarter swings and championship-clinching adjustments that decided the title." : "The league's most explosive offense meets the defense built to stop it. Inside the tactical battle that will define the season.");
              const heroAuthor = hero?.author?.display_name || "Bill Barnwell";
              const heroInitials = heroAuthor.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "BB";
              const heroRole = hero?.author?.archetype ? hero.author.archetype.replace(/_/g, " ") : "National Writer";
              const heroSlotLabel = hero?.slot ? hero.slot.toUpperCase() : (week === 24 ? "OFFSEASON LEAD · SUPER BOWL RECAP" : "GAME OF THE WEEK");

              return (
                <article className="hero-story">
                  <a href={`#article-${heroId}`} className="hero-image">
                    <img
                      src="https://images.unsplash.com/photo-1634306320557-4c4e4660692f?auto=format&fit=crop&w=1800&q=88"
                      alt="Football player celebrating under stadium lights"
                    />
                    <div className="hero-shade" />
                    <span className="hero-label">{heroSlotLabel}</span>
                    <span className="play-button" aria-hidden="true"><i /></span>
                  </a>
                  <div className="hero-content">
                    <div className="hero-meta"><span>{week === 24 ? "OFFSEASON LEAD READ" : "THE BIG READ"}</span><span>12 MIN READ</span></div>
                    <a href={`#article-${heroId}`}><h2>{heroTitle}</h2></a>
                    <p>{heroExcerpt}</p>
                    <div className="byline">
                      <span className="avatar red">{heroInitials}</span>
                      <span><small>DEEP DIVE BY</small><b>{heroAuthor}</b><em>{heroRole}</em></span>
                    </div>
                  </div>
                </article>
              );
            })()}

            {/* ALL-TIME NFL RECORD SPOTLIGHT */}
            {(() => {
              const records = newsData?.slotted?.historic_events;
              if (!Array.isArray(records) || records.length === 0) return null;
              return (
                <section className="historic-records-spotlight" aria-label="Historic NFL Record Breaking Coverage">
                  <div className="historic-records-header">
                    <span className="trophy-badge">🏆 ALL-TIME NFL RECORD BROKEN</span>
                    <span className="historic-subtitle">HISTORY WRITTEN ON THE GRIDIRON</span>
                  </div>
                  <div className="historic-records-grid">
                    {records.map((story: any) => {
                      const author = story.author?.display_name || "National Correspondent";
                      const initials = author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase();
                      return (
                        <article key={story.id} className="historic-record-spotlight-card">
                          <div className="historic-card-top">
                            <span className="record-slot-chip">{story.slot || "Historic Milestone"}</span>
                            <span className="record-exclusive-tag">COMMEMORATIVE FEATURE</span>
                          </div>
                          <a href={`#article-${story.id}`}>
                            <h3>{story.headline}</h3>
                          </a>
                          <p>{story.excerpt}</p>
                          <div className="author-row">
                            <span className="avatar amber">{initials}</span>
                            <span><b>{author}</b><small>HISTORICAL ANALYSIS · 9 MIN READ</small></span>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </section>
              );
            })()}

            {week === 24 ? (
              <div className="offseason-editorial-slate">
                {/* 2 SECONDARY STORIES */}
                <div className="latest-heading">
                  <h2>Secondary Slots (1 &amp; 2) · Retrospective &amp; Loss Analysis</h2>
                  <span style={{ fontSize: "11px", fontWeight: "bold", textTransform: "uppercase", letterSpacing: ".08em", color: "var(--gray-500)", fontFamily: "var(--font-mono)" }}>
                    Secondary Analysis · Slots 1 &amp; 2
                  </span>
                </div>
                <div className="story-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)" }}>
                  {(() => {
                    const secondary = (newsData?.slotted?.secondary && newsData.slotted.secondary.length > 0)
                      ? newsData.slotted.secondary
                      : [];

                    if (secondary.length === 0) {
                      return (
                        <div style={{ gridColumn: "1 / -1", padding: "28px", color: "var(--gray-500)", background: "var(--paper)", border: "1px solid var(--gray-300)" }}>
                          No secondary retrospective articles loaded for Week 24 yet.
                        </div>
                      );
                    }

                    return secondary.map((story: any, idx: number) => {
                      const isDb = Boolean(story.id);
                      const artLink = isDb ? `#article-${story.id}` : `#article-offseason-sec-${idx}`;
                      const title = story.headline || story.title;
                      const blurb = story.excerpt || story.blurb;
                      const author = story.author?.display_name || story.author || "National Reporter";
                      const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                      const kicker = story.article_type
                        ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                        : (story.slot || "Secondary Analysis");
                      const readTime = story.read || "8 min read";

                      return (
                        <article className="story-card" key={story.id || title} style={{ borderLeft: idx % 2 === 1 ? "1px solid var(--gray-300)" : undefined }}>
                          <span className="story-number">0{idx + 1}</span>
                          <div className="story-copy">
                            <span className="kicker">{kicker}</span>
                            <a href={artLink}><h3>{title}</h3></a>
                            <p>{blurb}</p>
                            <div className="author-row">
                              <span className="avatar">{initials}</span>
                              <span><b>{author}</b><small>NATIONAL REPORTER · {readTime}</small></span>
                            </div>
                          </div>
                        </article>
                      );
                    });
                  })()}
                </div>

                {/* 3 FEATURE STORIES */}
                <div className="latest-heading" style={{ marginTop: "44px" }}>
                  <h2>Feature Slots (3, 4 &amp; 5) · Offseason In-Depth Audits</h2>
                  <span style={{ fontSize: "11px", fontWeight: "bold", textTransform: "uppercase", letterSpacing: ".08em", color: "var(--gray-500)", fontFamily: "var(--font-mono)" }}>
                    Coaching · Free Agency · Draft · Slots 3, 4 &amp; 5
                  </span>
                </div>
                <div
                  className="offseason-features-grid"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3, 1fr)",
                    borderBottom: "1px solid var(--gray-300)"
                  }}
                >
                  {(() => {
                    const features = (newsData?.slotted?.features && newsData.slotted.features.length > 0)
                      ? newsData.slotted.features
                      : [];

                    if (features.length === 0) {
                      return (
                        <div style={{ gridColumn: "1 / -1", padding: "28px", color: "var(--gray-500)", background: "var(--paper)", border: "1px solid var(--gray-300)" }}>
                          No offseason feature articles loaded for Week 24 yet.
                        </div>
                      );
                    }

                    return features.map((story: any, idx: number) => {
                      const isDb = Boolean(story.id);
                      const artLink = isDb ? `#article-${story.id}` : `#article-offseason-feat-${idx}`;
                      const title = story.headline || story.title;
                      const blurb = story.excerpt || story.blurb;
                      const author = story.author?.display_name || story.author || "National Columnist";
                      const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                      const kicker = story.article_type
                        ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                        : (story.slot || "Feature Audit");
                      const readTime = story.read || "9 min read";

                      return (
                        <article
                          className="story-card"
                          key={story.id || title}
                          style={{
                            borderLeft: idx > 0 ? "1px solid var(--gray-300)" : undefined,
                            borderBottom: "none"
                          }}
                        >
                          <span className="story-number">0{idx + 3}</span>
                          <div className="story-copy">
                            <span className="kicker">{kicker}</span>
                            <a href={artLink}><h3 style={{ fontSize: "20px" }}>{title}</h3></a>
                            <p>{blurb}</p>
                            <div className="author-row">
                              <span className="avatar amber">{initials}</span>
                              <span><b>{author}</b><small>EXPERT ANALYST · {readTime}</small></span>
                            </div>
                          </div>
                        </article>
                      );
                    });
                  })()}
                </div>

                {/* OTHER ARTICLES (IF ANY) */}
                {Array.isArray(newsData?.slotted?.other) && newsData.slotted.other.length > 0 && (
                  <>
                    <div className="latest-heading" style={{ marginTop: "44px" }}>
                      <h2>Additional Coverage</h2>
                    </div>
                    <div className="story-grid">
                      {newsData.slotted.other.map((story: any, idx: number) => {
                        const isDb = Boolean(story.id);
                        const artLink = isDb ? `#article-${story.id}` : `#article-other-${idx}`;
                        const title = story.headline || story.title;
                        const blurb = story.excerpt || story.blurb;
                        const author = story.author?.display_name || story.author || "Contributor";
                        const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                        const kicker = story.article_type
                          ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                          : (story.slot || "Special Commission");

                        return (
                          <article className="story-card" key={story.id || title}>
                            <span className="story-number">0{idx + 6}</span>
                            <div className="story-copy">
                              <span className="kicker">{kicker}</span>
                              <a href={artLink}><h3>{title}</h3></a>
                              <p>{blurb}</p>
                              <div className="author-row">
                                <span className="avatar">{initials}</span>
                                <span><b>{author}</b><small>CONTRIBUTOR · 7 min read</small></span>
                              </div>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <>
                <div className="latest-heading">
                  <h2>{showAllStories ? "This Week's Stories" : "Latest Analysis"}</h2>
                  <button onClick={() => setShowAllStories(!showAllStories)}>
                    {showAllStories ? "Show featured" : "See all stories"} <span>{showAllStories ? "↑" : "→"}</span>
                  </button>
                </div>
                <div className={`story-grid ${showAllStories ? "expanded" : ""}`}>
                  {(() => {
                    const slotted = newsData?.slotted;
                    const dynamicStories: any[] = [];
                    if (slotted) {
                      if (Array.isArray(slotted.secondary)) dynamicStories.push(...slotted.secondary);
                      if (Array.isArray(slotted.features)) dynamicStories.push(...slotted.features);
                      if (Array.isArray(slotted.other)) dynamicStories.push(...slotted.other);
                    }

                    const storyList = dynamicStories.length > 0
                      ? (showAllStories ? dynamicStories : dynamicStories.slice(0, 3))
                      : (showAllStories ? stories : stories.slice(0, 3));

                    return storyList.map((story: any, index: number) => {
                      const isDb = Boolean(story.id);
                      const artLink = isDb ? `#article-${story.id}` : `#article-national-${index}`;
                      const title = story.headline || story.title;
                      const blurb = story.excerpt || story.blurb;
                      const author = story.author?.display_name || story.author || "National Reporter";
                      const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : story.initials;
                      const isRecord = story.article_type === "HISTORIC_RECORD_BREAK" || (story.slot && story.slot.toLowerCase().includes("record"));
                      const kicker = isRecord
                        ? "🏆 ALL-TIME NFL RECORD"
                        : story.article_type
                          ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                          : (story.kicker || "Analysis");
                      const readTime = story.read || "7 min read";

                      return (
                        <article className={`story-card ${index === 0 ? "feature-card" : ""} ${isRecord ? "record-breaker-card" : ""}`} key={story.id || title}>
                          <span className="story-number">0{index + 1}</span>
                          <div className="story-copy">
                            <span className="kicker">{kicker}</span>
                            <a href={artLink}><h3>{title}</h3></a>
                            <p>{blurb}</p>
                            <div className="author-row">
                              <span className={`avatar ${isRecord ? "amber" : ""}`}>{initials}</span>
                              <span><b>{author}</b><small>NATIONAL REPORTER · {readTime}</small></span>
                            </div>
                          </div>
                        </article>
                      );
                    });
                  })()}
                </div>
              </>
            )}
          </section>

          {week !== 24 && (
            <aside className="sidebar">
              <div className="sticky-panel">
                <div className="sidebar-title">
                  <span className="pulse" />
                  <div><span>POPULAR MARKETS</span><h2>Trending Local Coverage</h2></div>
                </div>
                <div className="market-list">
                  {(() => {
                    const primers = newsData?.slotted?.local_primers;
                    if (Array.isArray(primers) && primers.length > 0) {
                      return primers.map((story: any, index: number) => {
                        // Label by the team the primer is filed under; the opponent is often mentioned more
                        const primaryTeam = story.tagged_teams?.find((t: any) => t.id === story.team_id)
                          || story.tagged_teams?.find((t: any) => t.mention_type === 'PRIMARY') || story.tagged_teams?.[0];
                        const teamAbbr = primaryTeam?.abbr?.toLowerCase() || "nfl";
                        const teamName = primaryTeam?.name || "Local";
                        const author = story.author?.display_name || "Franchise Beat";

                        return (
                          <article className="market-story" key={story.id}>
                            <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                            <div className="market-copy">
                              <span className="team-tag"><img src={logo(teamAbbr)} alt="" />{teamName} Beat</span>
                              <a href={`#article-${story.id}`}><h3>{story.headline}</h3></a>
                              <p>By {author} <span>·</span> Beat Reporter</p>
                            </div>
                          </article>
                        );
                      });
                    }

                    return localStories.map((story, index) => (
                      <article className="market-story" key={story.title}>
                        <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                        <div className="market-copy">
                          <span className="team-tag"><img src={logo(story.team)} alt="" />{story.tag}</span>
                          <a href={`#article-local-${story.team}`}><h3>{story.title}</h3></a>
                          <p>By {story.author} <span>·</span> {story.time} ago</p>
                        </div>
                      </article>
                    ));
                  })()}
                </div>
                <a className="local-link" href="#social-teams">Explore all local markets <span>↗</span></a>
              </div>
              <div className="power-card">
                <span>POWER RANKINGS</span>
                <h3>{powerRankings?.week ? <>Week {powerRankings.week}<br />Top 3</> : <>Power<br />Rankings</>}</h3>
                <div className="power-teams">
                  {(powerRankings?.rankings || []).slice(0, 3).map((team) => (
                    <span key={team.team_id}><b>{String(team.rank).padStart(2, "0")}</b><img src={logo(team.abbr)} alt="" />{team.abbr}<em>{team.record || ""}</em></span>
                  ))}
                  {!powerRankings?.rankings?.length && <span>Published with this week's column</span>}
                </div>
                <a href="#rankings">Full rankings →</a>
              </div>
            </aside>
          )}
        </div>
      </main>}
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
