import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { logo } from "../../lib/gridiron";

export function ScoreTicker() {
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
