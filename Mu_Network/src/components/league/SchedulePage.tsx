import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { Chevron, getDivision, getTeamPosition, logo, type Team } from "../../lib/gridiron";

export function SchedulePage() {
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
              {matchups.filter((game) => game.day === day).map((game) => {
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

export function TeamSchedulePage({ team }: { team: Team }) {
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
