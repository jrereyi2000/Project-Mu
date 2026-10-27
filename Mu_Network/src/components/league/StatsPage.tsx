import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { Chevron, formatUpdated, logo } from "../../lib/gridiron";

export const offensiveStatCategories = ["Passing", "Rushing", "Receiving", "Scoring"];

// Stats page tabs by unit. Defense and special teams come from the Season Stats capture, which lists only the
// leaders in each category (defenders by tackles).
export const statCategoryGroups: Record<"team" | "player", { unit: string; categories: string[] }[]> = {
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

export const unitStatColumns: Record<string, string[]> = {
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
export const postseasonStatColumns: Record<string, string[]> = {
  Downs: ["3D CONV", "4D CONV", "2PT", "RZ ATT", "RZ TD", "RZ TD%", "PEN YDS"],
};

export const statColumns: Record<string, string[]> = {
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
export const HISTORY_CAPTURED_FROM = 2035;

export function StatsPage({ mode }: { mode: "team" | "player" }) {
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
