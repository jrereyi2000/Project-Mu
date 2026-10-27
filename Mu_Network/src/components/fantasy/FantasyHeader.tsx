import { useContext, useEffect, useRef, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerHeadshot, handleHeadshotError } from "../../lib/gridiron";
import { useFantasyPlayer } from "./FantasyPlayerContext";

export function FantasyHeader({ active }: { active: "news" | "rankings" }) {
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
