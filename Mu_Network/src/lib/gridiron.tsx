import type React from "react";
import { API_BASE } from "../api";

/** "Oct 15 · 8:03 AM" for the Stats page's Updated stamp (the latest capture load). */
export function formatUpdated(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}

export type Team = { name: string; abbr: string };

export const teams: Team[] = [
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

export const logo = (abbr: string) => {
  const clean = (abbr || "").toLowerCase();
  const normalized = clean === "az" ? "ari" : clean;
  return `https://a.espncdn.com/i/teamlogos/nfl/500/${normalized}.png`;
};

export const DEFAULT_AVATAR = "https://sleepercdn.com/images/v2/icons/player_default.webp";

// Bump whenever static/players/ is regenerated so browsers can't reuse previously cached headshots.
export const HEADSHOT_VERSION = "2026-10-12-crop";

export type HeadshotPlayer = { espn_id?: number | string | null; id?: number | string | null } | null | undefined;

// Ordered headshot sources: ESPN (NFL, then college for players still in school), then the generated
// headshot served by api.py from static/players/{player_id}.webp, then the placeholder.
export const getHeadshotCandidates = (player: HeadshotPlayer) => {
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

export const getPlayerHeadshot = (player: HeadshotPlayer) => getHeadshotCandidates(player)[0];

// <img onError> handler: advance to the next headshot source each time one fails to load.
export const handleHeadshotError = (e: React.SyntheticEvent<HTMLImageElement>, player: HeadshotPlayer) => {
  const img = e.currentTarget;
  const candidates = getHeadshotCandidates(player);
  const key = candidates.join("|");
  const attempt = img.dataset.headshotKey === key ? Number(img.dataset.headshotAttempt || 0) + 1 : 1;
  img.dataset.headshotKey = key;
  img.dataset.headshotAttempt = String(attempt);
  if (attempt < candidates.length) img.src = candidates[attempt];
  return attempt < candidates.length ? candidates[attempt] : null;
};

export const tickerGames = [
  ["4:25 PM", "KC", "kc", "27", "BUF", "buf", "24"],
  ["8:20 PM", "SF", "sf", "31", "SEA", "sea", "17"],
  ["4:25 PM", "DAL", "dal", "20", "PHI", "phi", "23"],
  ["1:00 PM", "BAL", "bal", "34", "CIN", "cin", "21"],
  ["8:15 PM", "DET", "det", "38", "GB", "gb", "24"],
];

export const stories = [
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

export const localStories = [
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

export const divisions: Record<string, string[]> = {
  "AFC East": ["buf", "mia", "ne", "nyj"],
  "AFC North": ["bal", "cin", "cle", "pit"],
  "AFC South": ["hou", "ind", "jax", "ten"],
  "AFC West": ["den", "kc", "lv", "lac"],
  "NFC East": ["dal", "nyg", "phi", "wsh"],
  "NFC North": ["chi", "det", "gb", "min"],
  "NFC South": ["atl", "car", "no", "tb"],
  "NFC West": ["ari", "lar", "sf", "sea"],
};

export const playerNames = [
  "Marcus Reed", "Devin Carter", "Jalen Brooks", "Tyson Mitchell", "Andre Hayes",
  "Caleb Foster", "Malik Robinson", "Darius Walker", "Trey Coleman", "Nolan Price",
  "Isaiah Bennett", "Cameron Ward",
];

export function getPlayerProfile(category: string, index: number) {
  const team = teams[(index * 5 + 2) % teams.length];
  const name = `${playerNames[index % playerNames.length]}${index >= playerNames.length ? ` ${index % 2 ? "Jr." : "II"}` : ""}`;
  const position = category === "passing" ? "QB" : category === "rushing" ? "HB" : category === "receiving" ? "WR" : category === "tightend" ? "TE" : category === "fullback" ? "FB" : ["QB", "HB", "WR", "TE", "FB"][index % 5];
  return { name, team, position, number: 2 + (index % 88) };
}

export function getPlayerIdForTeam(team: Team, slot: number) {
  const baseId = ((teams.indexOf(team) - 2) * 13 + 32) % 32;
  return baseId + slot * 32;
}

export function getDivision(abbr: string) {
  return Object.entries(divisions).find(([, members]) => members.includes(abbr))?.[0] ?? "NFL";
}

export function getTeamPosition(abbr: string) {
  const division = getDivision(abbr);
  const members = divisions[division];
  return members.indexOf(abbr) + 1;
}

export function Chevron({ direction = "down" }: { direction?: "down" | "up" | "left" | "right" }) {
  const rotation = direction === "left" ? "rotate(90 12 12)" : direction === "right" ? "rotate(-90 12 12)" : direction === "up" ? "rotate(180 12 12)" : "";
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="m7 9.5 5 5 5-5" transform={rotation} />
    </svg>
  );
}
