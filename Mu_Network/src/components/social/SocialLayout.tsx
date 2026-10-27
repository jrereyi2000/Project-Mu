import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { logo } from "../../lib/gridiron";
import {
  formatSocialCount,
  SOCIAL_API,
  SocialAvatar,
  socialHandlePath,
  SocialIcon,
  type SocialTrend,
  socialTrendHref,
  useSocialData,
} from "./socialShared";

export function SocialNavigation({ active }: { active: "home" | "explore" | "teams" }) {
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

export function SocialTrendsWidget({ limit = 6 }: { limit?: number }) {
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

export function SocialWhoToFollow() {
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

export function SocialNflWidget() {
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

export function SocialRightRail({ trends = true }: { trends?: boolean }) {
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
