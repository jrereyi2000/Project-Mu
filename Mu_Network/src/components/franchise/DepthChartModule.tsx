// #franchise Depth Chart module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.2): all 35 of the save's DepthChart
// slots (grouped offense / defense / special teams / situational), up to 6 players each. Click one player then
// another in the same slot to swap them (or drag one onto the other). "Let Staff Set" asks the coaching staff
// (POST /depth-chart/staff, depth_chart_engine) and shows the suggestion; Apply replaces the local chart. "Save"
// queues the chart (PUT /depth-chart); the league office writes it at its next save write. Shapes:
// types/franchiseRoster.ts.
import { useEffect, useMemo, useState } from "react";
import "../team/frontOffice.css";
import {
  DEPTH_CHART_SLOTS, DEPTH_PER_SLOT,
  type DepthChartEntry, type DepthChartResponse, type DepthChartSaveRequest, type QueuedMoveResponse,
} from "../../types/franchiseRoster";
import { SharedStyles, StateNote, franchiseFetch, useFranchiseData } from "./RosterCapModule";

type Chart = Record<string, DepthChartEntry[]>;

const GROUPS: [string, string[]][] = [
  ["Offense", ["QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT"]],
  ["Defense", ["LE", "RE", "DT", "LOLB", "MLB", "ROLB", "CB", "FS", "SS"]],
  ["Special Teams", ["K", "P", "LS", "KOS", "KR", "PR"]],
  ["Situational", ["3DRB", "PWHB", "GAD", "SLWR", "RLE", "RRE", "RDT", "NT", "SUBLB", "SLCB"]],
];

const SLOT_LABEL: Record<string, string> = {
  "3DRB": "3rd Down RB", PWHB: "Power HB", GAD: "Gadget", SLWR: "Slot WR", RLE: "Rush LE", RRE: "Rush RE",
  RDT: "Rush DT", NT: "Nose Tackle", SUBLB: "Sub LB", SLCB: "Slot CB", KOS: "Kickoff Specialist",
  KR: "Kick Returner", PR: "Punt Returner", LS: "Long Snapper", LOLB: "SAM", MLB: "MIKE", ROLB: "WILL",
};

const SOURCE_LABEL = { MADDEN: "Madden's chart", MU_COACH: "Coaching staff", JR: "Your chart" } as const;

const DC_CSS = `
.fr-dc-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 18px; }
.fr-dc-bar .fr-grow { flex: 1; color: var(--gray-500); font: 600 9px var(--font-mono); }
.fr-dc-group + .fr-dc-group { margin-top: 30px; }
.fr-dc-group h3 { margin: 0 0 10px; color: var(--gray-500); font: 700 9px var(--font-mono); letter-spacing: .1em; text-transform: uppercase; }
.fr-dc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.fr-slot { background: var(--white); border-top: 3px solid var(--ink); }
.fr-slot.warn { border-top-color: var(--red); }
.fr-slot.changed { box-shadow: inset 0 0 0 1px #e0a800; }
.fr-slot header { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; padding: 10px 12px 6px; }
.fr-slot header strong { font: 700 13px var(--font-mono); }
.fr-slot header small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; letter-spacing: .04em; }
.fr-slot ol { margin: 0; padding: 0 0 6px; list-style: none; }
.fr-slot li { display: grid; grid-template-columns: 18px 1fr auto; align-items: center; gap: 6px; min-height: 30px; padding: 0 12px; border-top: 1px solid var(--gray-100); cursor: pointer; user-select: none; }
.fr-slot li:hover { background: #f5f4f1; }
.fr-slot li.sel { background: var(--ink); color: var(--white); }
.fr-slot li.sel small, .fr-slot li.sel i { color: #c2c0bb; }
.fr-slot li.over { outline: 2px dashed var(--team-color, var(--red)); outline-offset: -2px; }
.fr-slot li i { color: var(--gray-300); font: 700 9px var(--font-mono); font-style: normal; }
.fr-slot li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 11px var(--font-sans); }
.fr-slot li small { color: var(--gray-500); font: 600 8px var(--font-mono); white-space: nowrap; }
.fr-slot li.injured span { color: var(--red); }
.fr-slot li.sel.injured span { color: #ff9aa9; }
.fr-inj { margin-left: 4px; padding: 1px 4px; border-radius: 2px; color: var(--white); background: var(--red); font: 700 7px var(--font-mono); }
.fr-slot .fr-empty-slot { padding: 8px 12px 10px; color: var(--gray-300); font: 600 9px var(--font-mono); }
.fr-suggest { margin-bottom: 22px; padding: 18px 20px; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); }
.fr-suggest h3 { margin: 0 0 4px; font: 700 18px var(--font-sans); letter-spacing: -.02em; }
.fr-suggest p { margin: 0 0 12px; color: #55575d; font: 500 10px/1.5 var(--font-mono); }
.fr-suggest ul { margin: 0 0 14px; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px 16px; max-height: 260px; overflow: auto; }
.fr-suggest li { font: 500 10px/1.5 var(--font-mono); color: #55575d; }
.fr-suggest li b { color: var(--ink); }
.fr-suggest footer { display: flex; gap: 8px; }
`;

function fill(slots: Chart): Chart {
  const out: Chart = {};
  for (const s of DEPTH_CHART_SLOTS) out[s] = (slots[s] ?? []).slice(0, DEPTH_PER_SLOT);
  return out;
}
const ids = (c: Chart, s: string) => (c[s] ?? []).map((e) => e.player_id).join(",");
const changedSlots = (a: Chart, b: Chart): string[] => DEPTH_CHART_SLOTS.filter((s) => ids(a, s) !== ids(b, s));

function SlotCard({ slot, entries, selected, changed, onPick, onSwap }: {
  slot: string;
  entries: DepthChartEntry[];
  selected: number | null;
  changed: boolean;
  onPick: (index: number) => void;
  onSwap: (from: number, to: number) => void;
}) {
  const [over, setOver] = useState<number | null>(null);
  const starterHurt = entries[0]?.injured ?? false;
  return (
    <article className={`fr-slot${starterHurt ? " warn" : ""}${changed ? " changed" : ""}`}>
      <header>
        <strong>{slot}</strong>
        <small>{starterHurt ? "Starter injured" : SLOT_LABEL[slot] ?? ""}</small>
      </header>
      {entries.length === 0 ? <div className="fr-empty-slot">No players set</div> : (
        <ol>
          {entries.map((e, i) => (
            <li key={`${e.player_id}-${i}`}
              className={`${selected === i ? "sel" : ""}${e.injured ? " injured" : ""}${over === i ? " over" : ""}`}
              draggable
              role="button"
              tabIndex={0}
              aria-pressed={selected === i}
              title={selected !== null && selected !== i ? "Click to swap" : "Click, then click another player in this slot to swap"}
              onClick={() => onPick(i)}
              onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && (ev.preventDefault(), onPick(i))}
              onDragStart={(ev) => { ev.dataTransfer.setData("text/plain", `${slot}:${i}`); ev.dataTransfer.effectAllowed = "move"; }}
              onDragOver={(ev) => { ev.preventDefault(); setOver(i); }}
              onDragLeave={() => setOver(null)}
              onDrop={(ev) => {
                ev.preventDefault();
                setOver(null);
                const [s, from] = ev.dataTransfer.getData("text/plain").split(":");
                if (s === slot && Number(from) !== i) onSwap(Number(from), i);
              }}>
              <i>{i + 1}</i>
              <span>{e.name}{e.injured && <b className="fr-inj">INJ</b>}</span>
              <small>{e.position} {e.overall ?? "—"}</small>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

export default function DepthChartModule({ teamAbbr }: { teamAbbr: string }) {
  const [loaded, reload] = useFranchiseData<DepthChartResponse>(teamAbbr, "/depth-chart");
  const [chart, setChart] = useState<Chart | null>(null);
  const [selected, setSelected] = useState<{ slot: string; index: number } | null>(null);
  const [suggestion, setSuggestion] = useState<Chart | null>(null);
  const [busy, setBusy] = useState<"staff" | "save" | null>(null);
  const [queued, setQueued] = useState<QueuedMoveResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const base = useMemo(() => (loaded.status === "ready" ? fill(loaded.data.slots) : null), [loaded]);
  useEffect(() => {
    setChart(base);
    setSelected(null);
    if (loaded.status === "ready") setQueued((q) => loaded.data.pending ?? q);
  }, [base, loaded]);

  const dirty = chart && base ? changedSlots(chart, base) : [];

  const pick = (slot: string, index: number) => {
    if (!chart) return;
    if (selected && selected.slot === slot && selected.index !== index) {
      swap(slot, selected.index, index);
      setSelected(null);
    } else if (selected && selected.slot === slot) {
      setSelected(null);
    } else {
      setSelected({ slot, index });
    }
  };
  const swap = (slot: string, a: number, b: number) => {
    setChart((c) => {
      if (!c) return c;
      const list = [...c[slot]];
      [list[a], list[b]] = [list[b], list[a]];
      return { ...c, [slot]: list };
    });
    setQueued(null);
  };

  const askStaff = () => {
    setBusy("staff");
    setActionError(null);
    franchiseFetch<DepthChartResponse>(teamAbbr, "/depth-chart/staff", "POST", {})
      .then((r) => setSuggestion(fill(r.slots)))
      .catch((e) => setActionError(e?.status === 404 ? "The staff suggestion isn't available yet (franchise API not mounted)." : `The staff couldn't set the chart: ${e.message}`))
      .finally(() => setBusy(null));
  };
  const save = () => {
    if (!chart) return;
    setBusy("save");
    setActionError(null);
    const body: DepthChartSaveRequest = { slots: Object.fromEntries(DEPTH_CHART_SLOTS.map((s) => [s, chart[s].map((e) => e.player_id)])) };
    franchiseFetch<QueuedMoveResponse>(teamAbbr, "/depth-chart", "PUT", body)
      .then((q) => { setQueued(q); reload(); })
      .catch((e) => setActionError(`Couldn't queue the chart: ${e.message}`))
      .finally(() => setBusy(null));
  };

  if (loaded.status !== "ready" || !chart || !base) {
    return <section className="team-data-view fo-view"><SharedStyles /><StateNote state={loaded} what="the depth chart" /></section>;
  }
  const data = loaded.data;
  const staffChanges = suggestion ? changedSlots(suggestion, chart) : [];
  const injuredStarters = DEPTH_CHART_SLOTS.filter((s) => chart[s][0]?.injured);

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-dc" precedence="default">{DC_CSS}</style>
      <div className="team-section-title">
        <span>{data.team} · Season {data.season} · Week {data.week} · {SOURCE_LABEL[data.source]}</span>
        <h2>Depth Chart</h2>
      </div>
      <div className="fr-dc-bar">
        <span className="fr-grow">
          {dirty.length > 0 ? `${dirty.length} slot${dirty.length === 1 ? "" : "s"} changed, not saved`
            : queued ? `Queued (move #${queued.move_id}, ${queued.status.toLowerCase()}): applied at the next league office write`
            : "Click a player, then another in the same slot, to swap them. Drag works too."}
          {injuredStarters.length > 0 && ` · Injured starters: ${injuredStarters.join(", ")}`}
        </span>
        <button className="fr-btn" disabled={busy !== null} onClick={askStaff}>{busy === "staff" ? "Asking staff…" : "Let Staff Set"}</button>
        <button className="fr-btn" disabled={dirty.length === 0 || busy !== null} onClick={() => { setChart(base); setSelected(null); }}>Reset</button>
        <button className="fr-btn primary" disabled={dirty.length === 0 || busy !== null} onClick={save}>{busy === "save" ? "Saving…" : "Save"}</button>
      </div>
      {actionError && <p className="fr-warn" role="alert">{actionError}</p>}
      {queued && dirty.length === 0 && <p className="fr-ok">Your chart is queued and will be applied at the next league office write.</p>}
      {suggestion && (
        <div className="fr-suggest">
          <h3>Staff suggestion</h3>
          <p>{staffChanges.length === 0 ? "The staff agrees with the current chart." : `The staff would change ${staffChanges.length} slot${staffChanges.length === 1 ? "" : "s"}:`}</p>
          {staffChanges.length > 0 && (
            <ul>
              {staffChanges.map((s) => (
                <li key={s}><b>{s}</b>: {suggestion[s].slice(0, 3).map((e) => e.name).join(", ") || "empty"}
                  {chart[s][0]?.player_id !== suggestion[s][0]?.player_id && <> (starter was {chart[s][0]?.name ?? "empty"})</>}
                </li>
              ))}
            </ul>
          )}
          <footer>
            <button className="fr-btn primary" disabled={staffChanges.length === 0}
              onClick={() => { setChart(suggestion); setSuggestion(null); setSelected(null); setQueued(null); }}>Apply</button>
            <button className="fr-btn" onClick={() => setSuggestion(null)}>Dismiss</button>
          </footer>
        </div>
      )}
      {GROUPS.map(([group, slots]) => (
        <section className="fr-dc-group" key={group}>
          <h3>{group}</h3>
          <div className="fr-dc-grid">
            {slots.map((s) => (
              <SlotCard key={s} slot={s} entries={chart[s]} changed={dirty.includes(s)}
                selected={selected?.slot === s ? selected.index : null}
                onPick={(i) => pick(s, i)} onSwap={(a, b) => { swap(s, a, b); setSelected(null); }} />
            ))}
          </div>
        </section>
      ))}
      <p className="fo-basis">Slot names are the save's DepthChart fields. Saving queues the chart; Project Mu writes it into the save on the league office's next run.</p>
    </section>
  );
}
