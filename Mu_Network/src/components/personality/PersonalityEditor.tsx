import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import type {
  CatalogueTrait,
  PersonalityCatalogue,
  PersonalityOverride,
  PersonalityProfile,
} from "../../types/personality";

// JR's on-page editor (docs/in_progress/PERSONALITY_DISPLAY_PLAN.md §2.2, D3): slider steppers with a live total
// that must stay in the band, a trait picker that greys out conflicts, full categories and ineligible traits, and a
// required reason. Save posts the audited core override; a reason starting "note:" also shows in "How he's
// changed" (Q2). These checks are for convenience: the server is the authority and its 409/422 message is shown.

const FALLBACK_BAND = { min: 24, max: 36 };

export function sliderTotal(sliders: Record<string, number>): number {
  return Object.values(sliders).reduce((a, b) => a + b, 0);
}

// Why a trait can't be added right now, or null when it can.
export function traitBlockReason(
  trait: CatalogueTrait,
  selected: string[],
  catalogue: PersonalityCatalogue,
): string | null {
  if (selected.includes(trait.id)) return null;
  const byId = new Map(catalogue.traits.map((t) => [t.id, t]));
  if (selected.length >= catalogue.max_traits) return `already ${catalogue.max_traits} traits`;
  for (const id of selected) {
    const other = byId.get(id);
    if (trait.conflicts.includes(id) || (other && other.conflicts.includes(trait.id))) {
      return `conflicts with ${other?.name || id}`;
    }
  }
  if (trait.category) {
    const inCategory = selected.filter((id) => byId.get(id)?.category === trait.category).length;
    if (inCategory >= catalogue.max_per_category) return `${trait.category.replace(/_/g, " ")} is full`;
  }
  return null;
}

type Props = {
  profile: PersonalityProfile;
  token: string;
  onCancel: () => void;
  onSaved: (next: PersonalityProfile) => void;
  catalogueFixture?: PersonalityCatalogue; // fixture hook: skip the catalogue fetch
};

export function PersonalityEditor({ profile, token, onCancel, onSaved, catalogueFixture }: Props) {
  const initialSliders = useMemo(
    () => Object.fromEntries(profile.sliders.map((s) => [s.name, s.value])) as Record<string, number>,
    [profile],
  );
  const initialTraits = useMemo(() => profile.traits.map((t) => t.id), [profile]);
  const [sliders, setSliders] = useState<Record<string, number>>(initialSliders);
  const [traits, setTraits] = useState<string[]>(initialTraits);
  const [reason, setReason] = useState("");
  const [catalogue, setCatalogue] = useState<PersonalityCatalogue | null>(catalogueFixture || null);
  const [catalogueError, setCatalogueError] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (catalogueFixture) return;
    fetch(`${API_BASE}/api/personality/catalogue?role=${encodeURIComponent(profile.kind)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((c: PersonalityCatalogue) => setCatalogue(c))
      .catch((e) => setCatalogueError(String(e?.message || e)));
  }, [profile.kind, catalogueFixture]);

  const band = catalogue?.slider_band || FALLBACK_BAND;
  const total = sliderTotal(sliders);
  const totalOk = total >= band.min && total <= band.max;
  const added = traits.filter((t) => !initialTraits.includes(t));
  const removed = initialTraits.filter((t) => !traits.includes(t));
  const changedSliders = Object.fromEntries(Object.entries(sliders).filter(([k, v]) => initialSliders[k] !== v));
  const changed = added.length > 0 || removed.length > 0 || Object.keys(changedSliders).length > 0;
  const canSave = changed && totalOk && reason.trim().length > 0 && !saving;

  const step = (name: string, delta: number) =>
    setSliders((s) => ({ ...s, [name]: Math.max(0, Math.min(10, (s[name] ?? 5) + delta)) }));

  const nameOf = (id: string) =>
    catalogue?.traits.find((t) => t.id === id)?.name || profile.traits.find((t) => t.id === id)?.name || id.replace(/_/g, " ");

  async function save() {
    setSaving(true);
    setMessage("");
    const body: PersonalityOverride = { reason: reason.trim() };
    if (Object.keys(changedSliders).length) body.sliders = changedSliders;
    if (added.length) body.add_traits = added;
    if (removed.length) body.remove_traits = removed;
    if (profile.revision !== null && profile.revision !== undefined) body.expected_revision = profile.revision;
    try {
      const r = await fetch(`${API_BASE}/api/personality/${profile.kind}/${encodeURIComponent(profile.ref)}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Token": token },
        body: JSON.stringify(body),
      });
      const json = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof json?.detail === "string" ? json.detail : JSON.stringify(json?.detail ?? `HTTP ${r.status}`);
        setMessage(r.status === 409 ? `Someone changed this profile first: ${detail}. Reload and try again.` : detail);
      } else {
        onSaved(json as PersonalityProfile);
      }
    } catch {
      setMessage("Couldn't reach the server.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="pe-editor">
      <h4>Edit personality</h4>
      <ul className="pe-sliders">
        {profile.sliders.map((s) => (
          <li key={s.name}>
            <span className="pe-name">{s.name}</span>
            <button type="button" aria-label={`lower ${s.name}`} onClick={() => step(s.name, -1)} disabled={sliders[s.name] <= 0}>
              −
            </button>
            <output>{sliders[s.name]}</output>
            <button type="button" aria-label={`raise ${s.name}`} onClick={() => step(s.name, 1)} disabled={sliders[s.name] >= 10}>
              +
            </button>
          </li>
        ))}
      </ul>
      <p className={`pe-total${totalOk ? "" : " bad"}`}>
        Total {total} (must stay {band.min}–{band.max})
      </p>

      <div className="pe-traits">
        <span className="pe-label">Traits</span>
        <div className="pe-selected">
          {traits.map((id) => (
            <button key={id} type="button" className="pp-chip on" onClick={() => setTraits(traits.filter((t) => t !== id))} title="Remove">
              {nameOf(id)} ×
            </button>
          ))}
        </div>
        {catalogueError && <p className="pe-msg">Trait list unavailable: {catalogueError}</p>}
        {catalogue && (
          <div className="pe-picker">
            {catalogue.traits
              .filter((t) => !traits.includes(t.id))
              .map((t) => {
                const why = traitBlockReason(t, traits, catalogue);
                return (
                  <button
                    key={t.id}
                    type="button"
                    className="pp-chip"
                    disabled={why !== null}
                    title={why || t.tendency || undefined}
                    onClick={() => setTraits([...traits, t.id])}
                  >
                    {t.name}
                  </button>
                );
              })}
          </div>
        )}
      </div>

      <label className="pe-reason">
        <span className="pe-label">Reason (required; start with "note:" to show it in his story)</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="why this change" />
      </label>

      {message && <p className="pe-msg">{message}</p>}
      <div className="pe-actions">
        <button type="button" className="pp-edit" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button type="button" className="pp-edit primary" onClick={save} disabled={!canSave}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

export default PersonalityEditor;
