import type React from "react";
import { useContext, useEffect, useRef, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerHeadshot, handleHeadshotError, logo, teams } from "../../lib/gridiron";

export type SearchTeamResult = {
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

export type SearchPlayerResult = {
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

export function GlobalSearchModal({ close }: { close: () => void }) {
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
