import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { Chevron, logo, teams } from "../../lib/gridiron";

export function MegaMenu({
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
