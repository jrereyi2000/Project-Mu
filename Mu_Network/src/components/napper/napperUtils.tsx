import React from "react";

// Known ESPN player IDs for headshots
export const ESPN_PLAYER_IDS: Record<string, string> = {
  "josh allen": "3918298",
  "dak prescott": "2577417",
  "jordan love": "4036378",
  "kenneth walker": "4567048",
  "kenneth walker iii": "4567048",
  "d'andre swift": "4259545",
  "david montgomery": "4035538",
  "nico collins": "4258169",
  "jaxon smith-njigba": "4430878",
  "puka nacua": "4426515",
  "justin jefferson": "4262921",
  "brock bowers": "4432665",
  "sam laporta": "4430027",
  "breece hall": "4427366",
  "bijan robinson": "4430807",
  "ceedee lamb": "4380150",
  "amon-ra st. brown": "4374302",
  "patrick mahomes": "3139477",
  "lamar jackson": "3916387",
  "aaron jones": "3042519",
  "jaylen waddle": "4372016",
  "drake maye": "4431452",
  "trey mcbride": "4361528",
  "t.j. hockenson": "4035537",
  "kyler murray": "3917315",
  "saquon barkley": "3929630",
  "christian mccaffrey": "3117251",
  "garrett wilson": "4429022",
  "george kittle": "3040151",
  "marvin harrison jr.": "4432708",
  "malik nabers": "4595348",
  "xavier worthy": "4432770",
  "brian thomas jr.": "4432773",
  "tank dell": "4372454",
  "devon achane": "4429013",
  "kyren williams": "4430737",
};

export const DEFAULT_AVATAR = "https://sleepercdn.com/images/v2/icons/player_default.webp";

export function getPlayerHeadshotUrl(name?: string, fallbackUrl?: string): string {
  if (fallbackUrl && fallbackUrl.startsWith("http") && !fallbackUrl.includes("placeholder")) {
    return fallbackUrl;
  }
  if (!name) return DEFAULT_AVATAR;
  const clean = name.toLowerCase().trim();
  const espnId = ESPN_PLAYER_IDS[clean];
  if (espnId) {
    return `https://a.espncdn.com/i/headshots/nfl/players/full/${espnId}.png`;
  }
  return DEFAULT_AVATAR;
}

export function handleImageError(e: React.SyntheticEvent<HTMLImageElement>) {
  const img = e.currentTarget;
  if (img.src !== DEFAULT_AVATAR) {
    img.src = DEFAULT_AVATAR;
  }
}

/** Position slot styling class */
export function getSlotBadgeClass(slot: string): string {
  const s = slot.toUpperCase();
  if (s.includes("QB")) return "slot-qb";
  if (s.includes("RB")) return "slot-rb";
  if (s.includes("WR")) return "slot-wr";
  if (s.includes("TE")) return "slot-te";
  if (s.includes("SUPERFLEX")) return "slot-superflex";
  if (s.includes("FLEX")) return "slot-flex";
  if (s.includes("K")) return "slot-k";
  if (s.includes("DEF")) return "slot-def";
  return "slot-bn";
}

// Sleeper-style robot avatar SVGs
export const BlueRobotAvatar: React.FC<{ size?: number; className?: string }> = ({ size = 42, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 100 100" className={`rounded-full shrink-0 ${className}`}>
    <circle cx="50" cy="50" r="48" fill="#132b45" stroke="#00ceb8" strokeWidth="3" />
    <rect x="30" y="32" width="40" height="36" rx="8" fill="#1f3d5f" stroke="#38bdf8" strokeWidth="2" />
    <circle cx="42" cy="48" r="5" fill="#00ceb8" />
    <circle cx="58" cy="48" r="5" fill="#00ceb8" />
    <rect x="42" y="58" width="16" height="3" rx="1.5" fill="#38bdf8" />
    <line x1="50" y1="32" x2="50" y2="22" stroke="#38bdf8" strokeWidth="3" />
    <circle cx="50" cy="20" r="4" fill="#00ceb8" />
    <rect x="24" y="44" width="4" height="12" rx="2" fill="#38bdf8" />
    <rect x="72" y="44" width="4" height="12" rx="2" fill="#38bdf8" />
  </svg>
);

export const PinkRobotAvatar: React.FC<{ size?: number; className?: string }> = ({ size = 42, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 100 100" className={`rounded-full shrink-0 ${className}`}>
    <circle cx="50" cy="50" r="48" fill="#381b2a" stroke="#ff4a6e" strokeWidth="3" />
    <rect x="30" y="32" width="40" height="36" rx="8" fill="#54243d" stroke="#ff6b87" strokeWidth="2" />
    <circle cx="42" cy="48" r="5" fill="#ff4a6e" />
    <circle cx="58" cy="48" r="5" fill="#ff4a6e" />
    <rect x="40" y="58" width="20" height="3" rx="1.5" fill="#ff6b87" />
    <line x1="50" y1="32" x2="50" y2="22" stroke="#ff6b87" strokeWidth="3" />
    <circle cx="50" cy="20" r="4" fill="#ff4a6e" />
    <rect x="24" y="44" width="4" height="12" rx="2" fill="#ff6b87" />
    <rect x="72" y="44" width="4" height="12" rx="2" fill="#ff6b87" />
  </svg>
);

export const NapperMoonIcon: React.FC<{ size?: number; className?: string }> = ({ size = 26, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
    {/* Stylized Sleeper-esque pillow / crescent moon with sleep mask dots */}
    <rect x="2" y="5" width="28" height="22" rx="7" fill="url(#napperGrad)" />
    <circle cx="11" cy="14" r="2.5" fill="#0d1722" />
    <circle cx="21" cy="14" r="2.5" fill="#0d1722" />
    <path d="M12 20C14 22 18 22 20 20" stroke="#0d1722" strokeWidth="2" strokeLinecap="round" />
    <defs>
      <linearGradient id="napperGrad" x1="2" y1="5" x2="30" y2="27" gradientUnits="userSpaceOnUse">
        <stop stopColor="#00ceb8" />
        <stop offset="1" stopColor="#2bd9c6" />
      </linearGradient>
    </defs>
  </svg>
);
