import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { Chevron, divisions, getDivision, logo, type Team, teams } from "../../lib/gridiron";

export function StandingsPage() {
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
