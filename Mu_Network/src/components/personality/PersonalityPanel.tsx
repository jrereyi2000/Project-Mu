import { useCallback, useEffect, useState, type ReactNode } from "react";
import { API_BASE } from "../../api";
import type { PersonalityKind, PersonalityProfile, PersonRef } from "../../types/personality";
import { PersonalityEditor } from "./PersonalityEditor";
import "./personality.css";

// Personality panel (docs/done/PERSONALITY_DISPLAY_PLAN.md §2.2): who this person is, in words. Sliders as
// sentences with a 0-10 bar, traits with what they tend to do (hover), this week's mood tag, how he has changed,
// his ties as labels, attachment, open promises and a public trade request. Blocks the API marks unavailable are
// hidden. JR's Edit button shows when a #debug token is stored and the profile is editable.

export const TOKEN_KEY = "mu_debug_token"; // the same token #debug stores (DebugPage.tsx)
export const SHOW_SLIDER_BARS = true; // plan R2 / Q1: one flag hides the bars and keeps the sentences

export function readDebugToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

const SLIDER_NAMES: Record<string, string> = {
  temperament: "Temperament",
  sociability: "Sociability",
  confidence: "Confidence",
  openness: "Openness",
  warmth: "Warmth",
  drive: "Drive",
};

const TRADE_STATUS: Record<string, string> = {
  ACTIVE: "Has asked for a trade",
  WITHDRAWN: "Withdrew his trade request",
  RESOLVED_TRADE: "Got his trade",
  CLOSED_EMPLOYMENT: "Trade request closed",
};

export function personHref(p: PersonRef | undefined | null): string | null {
  if (!p || !p.ref) return null;
  switch (p.kind) {
    case "player":
      return `#player-${p.ref}`;
    case "writer":
      return `#author-${p.ref}`;
    case "owner":
    case "scout":
      return `#team-${String(p.ref).toLowerCase()}`;
    default:
      return null;
  }
}

function PersonLink({ p }: { p: PersonRef | undefined | null }) {
  if (!p) return <span>someone</span>;
  const href = personHref(p);
  return href ? <a href={href}>{p.name}</a> : <span>{p.name}</span>;
}

type LoadState =
  | { state: "loading" }
  | { state: "hidden" }
  | { state: "none" }
  | { state: "error"; message: string }
  | { state: "ok"; profile: PersonalityProfile };

function fixtureState(fixture: PersonalityPanelProps["fixture"]): LoadState {
  if (fixture === undefined) return { state: "loading" };
  if (fixture === "hidden") return { state: "hidden" };
  if (fixture === "none") return { state: "none" };
  return { state: "ok", profile: fixture };
}

export type PersonalityPanelProps = {
  kind: PersonalityKind;
  refId: string | number;
  compact?: boolean; // staff cards: no heading
  // Fixture hook: render a given result without fetching (tests, previews).
  fixture?: PersonalityProfile | "hidden" | "none";
};

export function PersonalityPanel({ kind, refId, compact = false, fixture }: PersonalityPanelProps) {
  const [load, setLoad] = useState<LoadState>(() => fixtureState(fixture));
  const [editing, setEditing] = useState(false);
  const token = readDebugToken();

  const fetchProfile = useCallback(() => {
    if (fixture !== undefined) {
      setLoad(fixtureState(fixture));
      return;
    }
    setLoad({ state: "loading" });
    fetch(`${API_BASE}/api/personality/${kind}/${encodeURIComponent(String(refId))}`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (r.status === 404) {
          setLoad(body?.detail === "hidden_until_drafted" ? { state: "hidden" } : { state: "none" });
        } else if (!r.ok) {
          setLoad({ state: "error", message: typeof body?.detail === "string" ? body.detail : `HTTP ${r.status}` });
        } else {
          setLoad({ state: "ok", profile: body as PersonalityProfile });
        }
      })
      .catch(() => setLoad({ state: "error", message: "the personality service isn't reachable" }));
  }, [kind, refId, fixture]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const wrap = (body: ReactNode) => (
    <section className={`pp-panel${compact ? " pp-compact" : ""}`} aria-label="Personality">
      {!compact && <h3 className="pp-title">Personality</h3>}
      {body}
    </section>
  );

  if (load.state === "loading") return wrap(<p className="pp-state">Loading personality…</p>);
  if (load.state === "hidden") return wrap(<p className="pp-state">Personality revealed after the draft</p>);
  if (load.state === "none") return compact ? null : wrap(<p className="pp-state">No personality profile yet</p>);
  if (load.state === "error") return wrap(<p className="pp-state">Personality unavailable: {load.message}</p>);

  const p = load.profile;
  const canEdit = Boolean(token) && p.editable;

  if (editing) {
    return wrap(
      <PersonalityEditor
        profile={p}
        token={token}
        onCancel={() => setEditing(false)}
        onSaved={(next) => {
          setEditing(false);
          setLoad({ state: "ok", profile: next });
        }}
      />,
    );
  }

  const unavailable = p.unavailable || {};
  const show = (block: keyof typeof unavailable, value: unknown) => !(block in unavailable) && value !== null && value !== undefined;
  const moodTags = p.mood ? [p.mood.primary, ...p.mood.secondary].filter((t) => t && t !== "neutral") : [];

  return wrap(
    <>
      <div className="pp-head">
        {show("mood", p.mood) && moodTags.length > 0 && (
          <div className="pp-mood" title="This week's mood">
            {moodTags.map((t, i) => (
              <span key={t} className={`pp-mood-tag${i === 0 ? " primary" : ""}`}>
                {t}
              </span>
            ))}
          </div>
        )}
        {show("trade_request", p.trade_request) && p.trade_request && (
          <span className={`pp-trade${p.trade_request.current ? "" : " stale"}`}>
            {TRADE_STATUS[p.trade_request.status] || p.trade_request.status}
            {p.trade_request.public_since && ` · since ${p.trade_request.public_since.season}, week ${p.trade_request.public_since.tick}`}
          </span>
        )}
        {canEdit && (
          <button type="button" className="pp-edit" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
      </div>

      <ul className="pp-sliders">
        {p.sliders.map((s) => (
          <li key={s.name} className="pp-slider">
            <span className="pp-slider-name">{SLIDER_NAMES[s.name] || s.name}</span>
            {SHOW_SLIDER_BARS && (
              <span className="pp-bar" role="img" aria-label={`${s.value} of 10`} title={`${s.low} ↔ ${s.high}`}>
                <i style={{ width: `${Math.max(0, Math.min(10, s.value)) * 10}%` }} />
              </span>
            )}
            <span className={`pp-sentence${s.sentence ? "" : " muted"}`}>
              {s.sentence || "unremarkable"}
              {s.inferred && (
                <span
                  className="pp-inferred"
                  title="No direct evidence; estimated from his traits."
                  style={{ fontSize: "0.8em", fontStyle: "italic", opacity: 0.7 }}
                >
                  {" "}inferred
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>

      {p.traits.length > 0 && (
        <div className="pp-traits">
          {p.traits.map((t) => (
            <span key={t.id} className="pp-chip" title={t.tendency || undefined} tabIndex={0}>
              {t.name}
              {t.tendency && <span className="pp-tip">{t.tendency}</span>}
            </span>
          ))}
        </div>
      )}

      {show("attachment", p.attachment) && p.attachment && (
        <p className="pp-line">
          <b>{p.attachment.label}</b> {p.attachment.team ? `to ${p.attachment.team}` : ""}
        </p>
      )}

      {show("ties", p.ties) && p.ties && p.ties.length > 0 && (
        <div className="pp-block">
          <h4>Relationships</h4>
          <ul className="pp-list">
            {p.ties.map((t, i) => (
              <li key={i}>
                {t.label} <PersonLink p={t.target} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {show("promises", p.promises) && p.promises && p.promises.length > 0 && (
        <div className="pp-block">
          <h4>Open promises</h4>
          <ul className="pp-list">
            {p.promises.map((pr, i) => (
              <li key={i}>
                {pr.direction === "made_to_him" ? (
                  <>
                    <PersonLink p={pr.by} /> promised him: {pr.text}
                  </>
                ) : (
                  <>
                    He promised <PersonLink p={pr.to} />: {pr.text}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {show("changes", p.changes) && p.changes && p.changes.length > 0 && (
        <div className="pp-block">
          <h4>How he's changed</h4>
          <ul className="pp-list">
            {p.changes.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </>,
  );
}

export default PersonalityPanel;
