// Front Office tab: who runs the team (head coach, coordinators, GM) and where it's headed (direction, top needs),
// from the endpoint's staff block (server/front_office_api.py fetch_staff). Tendencies come from engines/coach_profile:
// pass lean and fourth down are measured in this league when there are games; man coverage is reputation.
import { ordinal } from "./format";

type Tendency = { tier: "Low" | "Medium" | "High"; meaning: string; measured: boolean };
type Coordinator = { role: "OC" | "DC"; name: string | null; scheme: string | null; playbook: string | null; since: number };
type Need = { group: string; label: string; reasons: string[] };
export type Staff = {
  head_coach: {
    name: string;
    slant: "OFFENSIVE" | "DEFENSIVE" | "CEO";
    since: number;
    season_with_team: number;
    replaced: { coach: string; seasons: string } | null;
    offense: string | null;
    defensive_front: string | null;
    tendencies: Partial<Record<"pass_lean" | "fourth_down_aggressiveness" | "man_coverage", Tendency>>;
    this_season: { regular_season: string; playoffs: string | null };
    record_with_team: string | null;
    playoff_record_with_team: string | null;
    career: { regular_season: string; win_pct: number | null; winning_seasons: number; playoffs: string; super_bowls: string } | null;
    super_bowls_with_team: string[];
    coach_of_the_year: number[];
  };
  coordinators: Coordinator[];
  gm: { name: string; bio: string | null; since: number; head_coach_too: boolean } | null;
  direction: "CONTEND" | "RETOOL" | "REBUILD" | null;
  needs: Need[];
  urgent_need: { group: string; label: string } | null;
};

const SLANT: Record<string, string> = { OFFENSIVE: "Offensive-minded", DEFENSIVE: "Defensive-minded", CEO: "CEO-style" };
const DIRECTION: Record<string, [string, string]> = {
  CONTEND: ["Contend", "Built to win now"],
  RETOOL: ["Retool", "Reshaping the roster on the fly"],
  REBUILD: ["Rebuild", "Playing for the future"],
};
const TENDENCY_LABEL = { pass_lean: "Pass lean", fourth_down_aggressiveness: "Fourth down", man_coverage: "Coverage" };
const ROLE = { OC: "Offensive Coordinator", DC: "Defensive Coordinator" };

function Stat({ label, value, note }: { label: string; value: string | null; note?: string | null }) {
  return (
    <div className="fo-stat">
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

export default function StaffCards({ staff, season }: { staff: Staff; season: number }) {
  const hc = staff.head_coach;
  const titles = [
    ...hc.super_bowls_with_team.filter((s) => s.endsWith("won")).map((s) => `${s.split(" ")[0]} champion`),
    ...hc.coach_of_the_year.map((y) => `${y} Coach of the Year`),
  ];
  const direction = staff.direction ? DIRECTION[staff.direction] : null;

  return (
    <section className="fo-block">
      <div className="team-section-title"><span>Season {season}</span><h2>Front Office &amp; Staff</h2></div>

      {(direction || staff.needs.length > 0) && (
        <div className="fo-direction">
          {direction && (
            <div className={`fo-direction-badge ${staff.direction!.toLowerCase()}`}>
              <span>Direction</span><strong>{direction[0]}</strong><small>{direction[1]}</small>
            </div>
          )}
          {staff.needs.length > 0 && (
            <div className="fo-needs">
              <span>Top needs{staff.urgent_need && <b className="fo-tag contract-year">Urgent: {staff.urgent_need.label}</b>}</span>
              <ol>
                {staff.needs.map((n) => (
                  <li key={n.group}><strong>{n.label}</strong>{n.reasons.length > 0 && <small>{n.reasons.join(" · ")}</small>}</li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      <div className="fo-staff">
        <article className="fo-card fo-hc">
          <header>
            <span>Head Coach{staff.gm?.head_coach_too ? " & General Manager" : ""}</span>
            <h3>{hc.name}</h3>
            <small>
              {SLANT[hc.slant] || hc.slant} · since {hc.since} ({ordinal(hc.season_with_team)} season)
              {hc.replaced && ` · replaced ${hc.replaced.coach} (${hc.replaced.seasons})`}
            </small>
            {titles.length > 0 && <div className="fo-titles">{titles.map((t) => <b className="fo-tag" key={t}>{t}</b>)}</div>}
          </header>
          <div className="fo-stats">
            <Stat label="This season" value={hc.this_season.regular_season} note={hc.this_season.playoffs ? `${hc.this_season.playoffs} playoffs` : null} />
            <Stat label="With team" value={hc.record_with_team}
              note={hc.record_with_team ? `${hc.playoff_record_with_team} playoffs` : "early seasons not on record"} />
            <Stat label="Career" value={hc.career?.regular_season ?? null}
              note={hc.career ? `${hc.career.playoffs} playoffs · ${hc.career.super_bowls} in Super Bowls` : null} />
          </div>
          <dl className="fo-schemes">
            <div><dt>Offense</dt><dd>{hc.offense || "—"}</dd></div>
            <div><dt>Defensive front</dt><dd>{hc.defensive_front || "—"}</dd></div>
          </dl>
          <ul className="fo-tendencies">
            {(Object.keys(TENDENCY_LABEL) as (keyof typeof TENDENCY_LABEL)[]).map((k) => {
              const t = hc.tendencies[k];
              if (!t) return null;
              return (
                <li key={k}>
                  <span>{TENDENCY_LABEL[k]}</span>
                  <b className={`fo-tier ${t.tier.toLowerCase()}`}>{t.tier}</b>
                  <p>{t.meaning}<small>{t.measured ? "measured this season" : "reputation"}</small></p>
                </li>
              );
            })}
          </ul>
        </article>

        {staff.coordinators.map((c) => (
          <article className="fo-card" key={c.role}>
            <header>
              <span>{ROLE[c.role]}</span>
              <h3>{c.name || `Interim ${c.role}`}</h3>
              <small>since {c.since}{!c.name && " · not named in league records"}</small>
            </header>
            <dl className="fo-schemes">
              <div><dt>Scheme</dt><dd>{c.scheme || "—"}</dd></div>
              <div><dt>Playbook</dt><dd>{c.playbook || "—"}</dd></div>
            </dl>
          </article>
        ))}

        {staff.gm && !staff.gm.head_coach_too && (
          <article className="fo-card fo-gm">
            <header>
              <span>General Manager</span>
              <h3>{staff.gm.name}</h3>
              <small>since {staff.gm.since}</small>
            </header>
            {staff.gm.bio && <p>{staff.gm.bio}</p>}
          </article>
        )}
        {staff.gm?.head_coach_too && staff.gm.bio && (
          <article className="fo-card fo-gm">
            <header>
              <span>Roster Control</span>
              <h3>{hc.name} has the final say</h3>
              <small>head coach and general manager since {staff.gm.since}</small>
            </header>
            <p>{staff.gm.bio}</p>
          </article>
        )}
      </div>
    </section>
  );
}
