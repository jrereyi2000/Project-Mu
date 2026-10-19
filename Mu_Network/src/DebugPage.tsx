import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { API_BASE } from "./api";

// #debug (reached from the header's LIVE badge; also from a phone on the home network): league state,
// every table (sortable, filterable, paginated, cells editable), the API's endpoints, the spec docs, and
// buttons that start the weekly runs. Backend: server/debug_api.py. Writes need the DEBUG_TOKEN from .env.

const DEBUG_API = `${API_BASE}/api/debug`;
const TOKEN_KEY = "mu_debug_token";

type Column = { column: string; type: string; nullable: boolean; generated: boolean; pk: boolean };
type TableRows = { table: string; kind: string; columns: Column[]; pk: string[]; total: number; page: number; page_size: number; rows: Record<string, unknown>[] };
type Job = { id: string; kind: string; kind_label: string; status: string; exit_code?: number | null; started_at: string; resume?: string | null; log_tail?: string; url_hash?: string; headline?: string; article_id?: number; target_desk?: string };
type Capture = { id: number; folder: string; mode: string; season: number; week: number | null; status: string; error: string | null; loaded_at: string | null };
type Resumable = { folder: string; mode: string; stage: string };
type DebugState = { state: { current_season: number; current_week: number }; database: string; captures: Capture[]; writes_enabled: boolean; running_job: Job | null; resumable: Resumable[] };
type NewsWeekSummary = { season: number; week: number; articles: number; tweets: number };
type NewsSummary = { current_season: number; current_week: number; summary: NewsWeekSummary[]; total_articles: number; total_tweets: number };

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

async function call<T>(path: string, init?: RequestInit, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Debug-Token"] = token;
  const r = await fetch(`${DEBUG_API}${path}`, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body?.detail ? String(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `HTTP ${r.status}`);
  return body as T;
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const btn = "px-3 py-1.5 text-xs font-bold uppercase tracking-wider border-2 border-[var(--ink)] bg-white hover:bg-[var(--ink)] hover:text-white disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-[var(--ink)]";
const btnRed = "px-3 py-2 text-xs font-bold uppercase tracking-wider text-white bg-[var(--red)] border-2 border-[var(--red)] hover:bg-[var(--ink)] hover:border-[var(--ink)] disabled:opacity-40";
const card = "bg-white border-t-4 border-[var(--ink)] p-4";
const label = "block text-[10px] font-bold uppercase tracking-widest text-[var(--gray-500)] mb-1";
const input = "border border-[var(--gray-300)] px-2 py-1 text-sm bg-white w-full";

// ------------------------------------------------------------- control ----
const RUNS: { kind: string; title: string; note: string }[] = [
  { kind: "scan_week", title: "Scan week", note: "Full weekly run: capture Season Stats, Team Stats and last week's schedule, extract, load, engines, articles. Playoffs (week 19 on): last round's schedule, next round's matchups and every game's box score (~7 min a game) instead of Season Stats. Week 22 (Pro Bowl week): no console, just the Super Bowl week's articles." },
  { kind: "publish_articles", title: "Publish articles", note: "Runs the newsroom swarm for the next unpublished week (national, fantasy, and Agent Wire slates)." },
  { kind: "backfill_week", title: "Backfill previous week", note: "The same capture, extract, load and engines. No articles." },
  { kind: "game_scan", title: "Game scan", note: "Every game of last week: box scores and both teams' player stats (~7 min a game), loaded over the backfill's lines. No articles." },
  { kind: "season_scan", title: "Season scan", note: "Annual start-of-season cadence: captures the 18-week schedule, all 32 team rosters, every team's contracts (Team Salary, right after rosters), and the draft prospect board (in that order; prospects last). No articles." },
  { kind: "roster_scan", title: "Roster scan", note: "Captures all 32 team rosters, extracts player cards, and updates players/seasons in the database (normally run once a season with schedule and draft board). No articles." },
  { kind: "contract_scan", title: "Contract scan", note: "Captures every team's Team Salary screen (contracts and cap), extracts and loads it; a re-scan supersedes the old contracts. ~40 min. Normally part of the Season scan; only loads in-season (the year on screen must match the league season). No articles." },
  { kind: "draft_scan", title: "Draft prospect scan", note: "Scans the scouting draft board line-by-line, extracts prospects via Apple Vision OCR, and syncs data/draft/ prospects board (normally run once a season with schedule and roster scans, after both). No articles." },
];

function StatusPill({ status }: { status: string }) {
  const color = status === "running" ? "bg-amber-400 text-black" : status === "done" ? "bg-emerald-600 text-white" : status === "stopped" ? "bg-[var(--gray-500)] text-white" : "bg-[var(--red)] text-white";
  return <span className={`px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${color}`}>{status}</span>;
}

function Control({ token, data, reload }: { token: string; data: DebugState | null; reload: () => void }) {
  const [season, setSeason] = useState("");
  const [week, setWeek] = useState("");
  const [resume, setResume] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [log, setLog] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (data) {
      const s = String(data.state.current_season);
      setSeason(s);
      setWeek(String(data.state.current_week));
      setOffseasonSeason(prev => prev || s);
      setOffseasonManifestPath(prev => prev || `captures/offseason/${s}/manifest.json`);
    }
  }, [data?.state.current_season, data?.state.current_week]);

  const loadJobs = useCallback(() => {
    call<Job[]>("/jobs").then(js => {
      setJobs(js);
      const current = js.find(j => j.status === "running") || js[0];
      if (current) call<{ log: string }>(`/jobs/${current.id}/log?lines=300`).then(r => setLog(r.log)).catch(() => {});
    }).catch(e => setMessage(String(e.message || e)));
  }, []);

  useEffect(() => {
    loadJobs();
    const t = window.setInterval(loadJobs, 3000);
    return () => window.clearInterval(t);
  }, [loadJobs]);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const running = jobs.find(j => j.status === "running");
  const latest = running || jobs[0];

  const saveState = async () => {
    setBusy(true); setMessage("");
    try {
      await call("/state", { method: "PUT", body: JSON.stringify({ current_season: Number(season), current_week: Number(week) }) }, token);
      setMessage("League state saved."); reload();
    } catch (e) { setMessage(`Not saved: ${(e as Error).message}`); }
    setBusy(false);
  };

  const start = async (kind: string, title: string) => {
    const what = resume ? `${title}, resuming ${resume}` : title;
    if (!window.confirm(`${what}?\n\nRemote Play takes over the console and your controller disconnects. Your phone gets a notice when it's your turn again.`)) return;
    setBusy(true); setMessage("");
    try {
      await call("/jobs", { method: "POST", body: JSON.stringify({ kind, resume: resume || null }) }, token);
      setMessage(`${what} started.`); setResume(""); loadJobs(); reload();
    } catch (e) { setMessage(`Not started: ${(e as Error).message}`); }
    setBusy(false);
  };

  const stop = async (id: string) => {
    if (!window.confirm("Stop this run? The capture stops where it is (every key is released); resume it later from its folder.")) return;
    try { await call(`/jobs/${id}/stop`, { method: "POST" }, token); setMessage("Stopping…"); loadJobs(); }
    catch (e) { setMessage(`Not stopped: ${(e as Error).message}`); }
  };

  const [news, setNews] = useState<NewsSummary | null>(null);
  const [targetWeek, setTargetWeek] = useState<string>("current");
  const [newsScope, setNewsScope] = useState<"ARTICLE" | "ALL">("ARTICLE");
  const [clearingNews, setClearingNews] = useState(false);

  // Custom story commissioning state (Two-Step Flow)
  const [promptText, setPromptText] = useState("");
  const [promptSeason, setPromptSeason] = useState("");
  const [promptWeek, setPromptWeek] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [commissioning, setCommissioning] = useState(false);
  const [customPlan, setCustomPlan] = useState<{
    classification: {
      target_desk: "NATIONAL" | "FANTASY";
      article_type_id: string;
      article_type_name: string;
      writer_archetype: string;
      team_id: number | null;
      desk_note: string;
      rationale: string;
    };
    catalog: {
      national_types: { id: string; name: string; allowed_writers: string[] }[];
      fantasy_types: { id: string; name: string; allowed_writers: string[] }[];
      national_writers: { id: string; name: string }[];
      fantasy_writers: { id: string; name: string }[];
      teams: { id: number; city: string; name: string; abbr: string }[];
    };
  } | null>(null);

  const [formDesk, setFormDesk] = useState<"NATIONAL" | "FANTASY">("NATIONAL");
  const [formType, setFormType] = useState("");
  const [formWriter, setFormWriter] = useState("");
  const [formTeam, setFormTeam] = useState<string>("");
  const [formDeskNote, setFormDeskNote] = useState("");

  // Offseason Package state
  const [offseasonSeason, setOffseasonSeason] = useState("");
  const [offseasonManifestPath, setOffseasonManifestPath] = useState("");
  const [offseasonDryRun, setOffseasonDryRun] = useState(false);
  const [offseasonOnly, setOffseasonOnly] = useState("");
  const [previewingOffseason, setPreviewingOffseason] = useState(false);
  const [runningOffseason, setRunningOffseason] = useState(false);
  const [offseasonPreviewData, setOffseasonPreviewData] = useState<any>(null);

  const loadNews = useCallback(() => {
    call<NewsSummary>("/news").then(setNews).catch(() => {});
  }, []);

  useEffect(() => {
    loadNews();
  }, [loadNews]);

  const curWeek = data?.state.current_week ?? Number(week) ?? 1;
  const curSeason = data?.state.current_season ?? Number(season) ?? 2035;

  let targetWeekNum: number | null = null;
  let targetSeasonNum: number | null = curSeason;
  let targetLabel = "";
  let targetArticles = 0;
  let targetTweets = 0;

  if (targetWeek === "current") {
    targetWeekNum = curWeek;
    targetLabel = `Week ${curWeek}`;
    const row = news?.summary.find(s => s.season === curSeason && s.week === curWeek);
    targetArticles = row?.articles || 0;
    targetTweets = row?.tweets || 0;
  } else if (targetWeek === "all_season") {
    targetWeekNum = null;
    targetLabel = `Season ${curSeason} (all weeks)`;
    const rows = (news?.summary || []).filter(s => s.season === curSeason);
    targetArticles = rows.reduce((acc, r) => acc + r.articles, 0);
    targetTweets = rows.reduce((acc, r) => acc + r.tweets, 0);
  } else if (targetWeek === "all_time") {
    targetWeekNum = null;
    targetSeasonNum = null;
    targetLabel = "All seasons & weeks";
    targetArticles = news?.total_articles || 0;
    targetTweets = news?.total_tweets || 0;
  } else {
    const w = Number(targetWeek);
    targetWeekNum = w;
    targetLabel = `Week ${w}`;
    const row = news?.summary.find(s => s.season === curSeason && s.week === w);
    targetArticles = row?.articles || 0;
    targetTweets = row?.tweets || 0;
  }

  const itemsToClear = newsScope === "ALL" ? (targetArticles + targetTweets) : targetArticles;

  const handleClearNews = async () => {
    if (!token) return;
    const desc = newsScope === "ALL" ? "articles & Agent Wire posts" : "articles";
    const confirmMsg = `Clear ${itemsToClear} ${desc} for ${targetLabel}?\n\nThis removes articles from news_feed, cascading to tags, power rankings, and agent memory entries so the slate can be rerun cleanly.`;
    if (!window.confirm(confirmMsg)) return;

    setClearingNews(true);
    setMessage("");
    try {
      const res = await call<{ deleted: number; remaining_articles: number; backup_file: string | null }>(
        "/news/clear",
        {
          method: "POST",
          body: JSON.stringify({
            season: targetSeasonNum,
            week: targetWeekNum,
            content_type: newsScope,
            backup: true,
          }),
        },
        token
      );
      setMessage(`Cleared ${res.deleted} items for ${targetLabel}. ${res.remaining_articles} articles remain in DB.${res.backup_file ? ` (Backup: ${res.backup_file})` : ""}`);
      loadNews();
      reload();
    } catch (e) {
      setMessage(`Clear failed: ${(e as Error).message}`);
    } finally {
      setClearingNews(false);
    }
  };

  const handlePublishNews = async () => {
    if (!token || !!running) return;
    const w = targetWeekNum !== null ? targetWeekNum : curWeek;
    if (!window.confirm(`Publish articles for Week ${w}?\n\nStarts the newsroom swarm (national, fantasy, and Agent Wire slates).`)) return;
    setBusy(true);
    setMessage("");
    try {
      await call("/jobs", { method: "POST", body: JSON.stringify({ kind: "publish_articles", week: w }) }, token);
      setMessage(`Newsroom run for Week ${w} started.`);
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Not started: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (data) {
      if (!promptSeason) setPromptSeason(String(data.state.current_season));
      if (!promptWeek) setPromptWeek(String(data.state.current_week));
    }
  }, [data?.state.current_season, data?.state.current_week, promptSeason, promptWeek]);

  const handleAnalyzePrompt = async () => {
    if (!token || !promptText.trim()) return;
    setAnalyzing(true);
    setMessage("");
    try {
      const res = await call<NonNullable<typeof customPlan>>(
        "/classify_article",
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptText.trim(),
            season: Number(promptSeason) || curSeason,
            week: Number(promptWeek) || curWeek,
          }),
        },
        token
      );
      setCustomPlan(res);
      setFormDesk(res.classification.target_desk);
      setFormType(res.classification.article_type_id);
      setFormWriter(res.classification.writer_archetype);
      setFormTeam(res.classification.team_id !== null ? String(res.classification.team_id) : "");
      setFormDeskNote(res.classification.desk_note);
      setMessage("Prompt analyzed. Review and adjust parameters below, then commission.");
    } catch (e) {
      setMessage(`Analysis failed: ${(e as Error).message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleCommissionStory = async () => {
    if (!token || !customPlan) return;
    setCommissioning(true);
    setMessage("");
    try {
      const s = Number(promptSeason) || curSeason;
      const w = Number(promptWeek) || curWeek;
      await call(
        "/commission_article",
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptText.trim(),
            season: s,
            week: w,
            target_desk: formDesk,
            article_type_id: formType,
            writer_archetype: formWriter,
            team_id: formTeam ? Number(formTeam) : null,
            desk_note: formDeskNote.trim() || promptText.trim(),
          }),
        },
        token
      );
      setMessage(`Commissioned ${formDesk} article. Log streaming below.`);
      setCustomPlan(null);
      setPromptText("");
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Commission failed: ${(e as Error).message}`);
    } finally {
      setCommissioning(false);
    }
  };

  const handleOffseasonSeasonChange = (val: string) => {
    setOffseasonSeason(val);
    if (!offseasonManifestPath || offseasonManifestPath.startsWith("captures/offseason/")) {
      setOffseasonManifestPath(`captures/offseason/${val}/manifest.json`);
    }
  };

  const handleValidateOffseason = async () => {
    if (!token) return;
    setPreviewingOffseason(true);
    setMessage("");
    setOffseasonPreviewData(null);
    try {
      const s = Number(offseasonSeason) || curSeason;
      const res = await call<any>(
        "/offseason/preview",
        {
          method: "POST",
          body: JSON.stringify({
            season: s,
            manifest_path: offseasonManifestPath.trim(),
            dry_run: offseasonDryRun,
            only: offseasonOnly || null,
          }),
        },
        token
      );
      setOffseasonPreviewData(res);
      setMessage("Offseason manifest validated successfully.");
    } catch (e) {
      setMessage(`Manifest preview failed: ${(e as Error).message}`);
    } finally {
      setPreviewingOffseason(false);
    }
  };

  const handleRunOffseason = async () => {
    if (!token || !!running) return;
    const s = Number(offseasonSeason) || curSeason;
    const mode = offseasonDryRun ? "DRY-RUN " : "";
    const scope = offseasonOnly ? `(${offseasonOnly})` : "(Full Package)";
    if (!window.confirm(`Run ${mode}Offseason Editorial Swarm for Season ${s} ${scope}?\n\nManifest: ${offseasonManifestPath}`)) return;

    setRunningOffseason(true);
    setBusy(true);
    setMessage("");
    try {
      const res = await call<any>(
        "/offseason/run",
        {
          method: "POST",
          body: JSON.stringify({
            season: s,
            manifest_path: offseasonManifestPath.trim(),
            dry_run: offseasonDryRun,
            only: offseasonOnly || null,
          }),
        },
        token
      );
      setMessage(res?.message || `Offseason swarm run started for Season ${s}.`);
      loadJobs();
      reload();
    } catch (e) {
      setMessage(`Offseason run failed: ${(e as Error).message}`);
    } finally {
      setRunningOffseason(false);
      setBusy(false);
    }
  };

  const resumable = data?.resumable || [];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <div className="grid gap-4 content-start">
        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">League state</h3>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <label><span className={label}>Season</span><input className={input} inputMode="numeric" value={season} onChange={e => setSeason(e.target.value)} /></label>
            <label><span className={label}>Current week</span><input className={input} inputMode="numeric" value={week} onChange={e => setWeek(e.target.value)} /></label>
          </div>
          <button className={btn} disabled={busy || !token} onClick={saveState}>Save</button>
          <p className="text-xs text-[var(--gray-500)] mt-2">Current week = Madden's next unplayed week (19–23 = postseason). The weekly load sets it; edit it only to fix it.</p>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Newsroom & Articles</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">
              {news ? `${news.total_articles} articles · ${news.total_tweets} posts` : "Loading..."}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 mb-3">
            <label>
              <span className={label}>Target</span>
              <select className={input} value={targetWeek} onChange={e => setTargetWeek(e.target.value)}>
                <option value="current">Current week (Wk {curWeek})</option>
                {Array.from({ length: 24 }, (_, i) => i + 1).map(w => (
                  <option key={w} value={String(w)}>
                    Week {w}{w === 24 ? " (Offseason)" : ""}{w === curWeek ? " (current)" : ""}
                  </option>
                ))}
                <option value="all_season">All weeks (Season {curSeason})</option>
                <option value="all_time">All seasons & weeks</option>
              </select>
            </label>
            <label>
              <span className={label}>Scope</span>
              <select className={input} value={newsScope} onChange={e => setNewsScope(e.target.value as "ARTICLE" | "ALL")}>
                <option value="ARTICLE">Articles only (keep tweets)</option>
                <option value="ALL">Articles & Agent Wire posts</option>
              </select>
            </label>
          </div>

          <div className="bg-[var(--paper)] p-2.5 mb-3 text-xs border border-[var(--gray-200)] flex items-center justify-between">
            <div>
              <span className="font-bold">{targetLabel}:</span>{" "}
              <span>{targetArticles} articles, {targetTweets} posts</span>
            </div>
            <StatusPill status={targetArticles > 0 ? "done" : "stopped"} />
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              className={btnRed}
              disabled={clearingNews || busy || !token || itemsToClear === 0}
              onClick={handleClearNews}
            >
              {clearingNews ? "Clearing..." : `Clear ${targetLabel} (${itemsToClear})`}
            </button>
            <button
              className={btn}
              disabled={busy || !token || !!running}
              onClick={handlePublishNews}
              title={`Run newsroom swarm for ${targetWeekNum ? `Week ${targetWeekNum}` : `Week ${curWeek}`}`}
            >
              Publish {targetWeekNum ? `Wk ${targetWeekNum}` : `Wk ${curWeek}`} Slate
            </button>
          </div>

          <p className="text-xs text-[var(--gray-500)] mt-2">
            Clearing articles removes article tags, power rankings, and agent memory takes, allowing the week to be rerun cleanly.
          </p>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Offseason Editorial Package</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">Week 24 Slate</span>
          </div>
          <p className="text-xs text-[var(--gray-600)] mb-3">
            Publish the 6-article offseason editorial package (Super Bowl recap &amp; retrospectives, coaching carousel audit, marquee signings, and first-round draft recap).
          </p>

          <div className="grid gap-3">
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className={label}>Season</span>
                <input
                  className={input}
                  inputMode="numeric"
                  value={offseasonSeason}
                  onChange={e => handleOffseasonSeasonChange(e.target.value)}
                  disabled={previewingOffseason || runningOffseason || busy}
                />
              </label>

              <label>
                <span className={label}>Sub-Event Scope (--only)</span>
                <select
                  className={input}
                  value={offseasonOnly}
                  onChange={e => setOffseasonOnly(e.target.value)}
                  disabled={previewingOffseason || runningOffseason || busy}
                >
                  <option value="">All (Full 6-Article Package)</option>
                  <option value="super_bowl">super_bowl (SB Recap, Champ, Runner-Up)</option>
                  <option value="coaching">coaching (Coaching Carousel Audit)</option>
                  <option value="signings">signings (Marquee Signing Grades)</option>
                  <option value="draft">draft (First-Round Draft Recap)</option>
                </select>
              </label>
            </div>

            <label>
              <span className={label}>Manifest Path</span>
              <input
                className={input}
                value={offseasonManifestPath}
                onChange={e => setOffseasonManifestPath(e.target.value)}
                placeholder="captures/offseason/2035/manifest.json"
                disabled={previewingOffseason || runningOffseason || busy}
              />
            </label>

            <div className="flex items-center gap-2 py-1">
              <input
                type="checkbox"
                id="offseason-dry-run"
                checked={offseasonDryRun}
                onChange={e => setOffseasonDryRun(e.target.checked)}
                disabled={previewingOffseason || runningOffseason || busy}
                className="w-4 h-4 text-[var(--red)] border-[var(--gray-300)] rounded cursor-pointer"
              />
              <label htmlFor="offseason-dry-run" className="text-xs font-bold uppercase tracking-wider text-[var(--ink)] cursor-pointer select-none">
                Dry Run (<code className="font-mono text-[11px] font-normal">--dry-run</code>)
              </label>
            </div>

            <div className="flex flex-wrap gap-2 pt-1">
              <button
                className={btn}
                disabled={previewingOffseason || runningOffseason || busy || !token || !offseasonManifestPath.trim()}
                onClick={handleValidateOffseason}
              >
                {previewingOffseason ? "Validating Manifest…" : "Validate / Preview Manifest"}
              </button>
              <button
                className={btnRed}
                disabled={previewingOffseason || runningOffseason || busy || !token || !!running || !offseasonManifestPath.trim()}
                onClick={handleRunOffseason}
              >
                {runningOffseason ? "Dispatching Swarm…" : "Run Offseason Swarm"}
              </button>
              {offseasonPreviewData && (
                <button
                  className="px-2 py-1 text-xs border border-[var(--gray-300)] text-[var(--gray-500)] hover:text-black"
                  onClick={() => setOffseasonPreviewData(null)}
                >
                  Dismiss Preview
                </button>
              )}
            </div>

            {offseasonPreviewData && (
              <div className="bg-[var(--paper)] p-3 text-xs border border-[var(--gray-200)] mt-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-bold uppercase text-[10px] tracking-wider text-[var(--ink)]">Manifest Preview Result</span>
                  <StatusPill status={offseasonPreviewData.valid !== false && !offseasonPreviewData.error ? "done" : "stopped"} />
                </div>
                {offseasonPreviewData.error && (
                  <div className="text-[var(--red)] font-bold mb-2">Error: {String(offseasonPreviewData.error)}</div>
                )}
                {offseasonPreviewData.summary && (
                  <div className="text-[var(--ink)] mb-2 font-medium">{String(offseasonPreviewData.summary)}</div>
                )}
                <pre className="max-h-60 overflow-y-auto text-[11px] font-mono bg-white p-2 border border-[var(--gray-200)] text-[var(--gray-700)]">
                  {JSON.stringify(offseasonPreviewData, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </section>

        <section className={card}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-black uppercase tracking-wide">Commission Custom Story</h3>
            <span className="text-[10px] font-mono text-[var(--gray-500)]">AI Editorial Desk</span>
          </div>
          <p className="text-xs text-[var(--gray-600)] mb-3">
            Give a story prompt or topic. Step 1 uses Gemini to classify the angle and assign an appropriate writer. Step 2 lets you review or adjust parameters before dispatching the ReAct swarm.
          </p>

          {!customPlan ? (
            <div className="grid gap-3">
              <label>
                <span className={label}>Story Prompt / Assignment</span>
                <textarea
                  className={`${input} min-h-[75px] resize-y font-sans`}
                  placeholder="e.g. Deep dive into Caleb Williams' performance under pressure vs man coverage, or who to target on fantasy waivers for the playoffs..."
                  value={promptText}
                  onChange={e => setPromptText(e.target.value)}
                  disabled={analyzing || busy}
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Season</span>
                  <input
                    className={input}
                    inputMode="numeric"
                    value={promptSeason}
                    onChange={e => setPromptSeason(e.target.value)}
                    disabled={analyzing || busy}
                  />
                </label>
                <label>
                  <span className={label}>Publication Week</span>
                  <input
                    className={input}
                    inputMode="numeric"
                    value={promptWeek}
                    onChange={e => setPromptWeek(e.target.value)}
                    disabled={analyzing || busy}
                  />
                </label>
              </div>
              <button
                className={btn}
                disabled={analyzing || busy || !token || !promptText.trim()}
                onClick={handleAnalyzePrompt}
              >
                {analyzing ? "Analyzing Prompt with Gemini…" : "Step 1: Analyze Prompt & Plan"}
              </button>
            </div>
          ) : (
            <div className="grid gap-3 border-t border-[var(--gray-200)] pt-3">
              <div className="bg-[var(--paper)] p-2.5 text-xs border border-[var(--gray-200)]">
                <div className="font-bold text-[10px] uppercase text-[var(--ink)] mb-1">Proposed Angle & Rationale</div>
                <p className="text-[var(--gray-700)] italic">{customPlan.classification.rationale}</p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Target Desk</span>
                  <select
                    className={input}
                    value={formDesk}
                    onChange={e => {
                      const newDesk = e.target.value as "NATIONAL" | "FANTASY";
                      setFormDesk(newDesk);
                      const available = newDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types;
                      if (available.length > 0) {
                        setFormType(available[0].id);
                        const writers = newDesk === "FANTASY" ? customPlan.catalog.fantasy_writers : customPlan.catalog.national_writers;
                        setFormWriter(writers[0]?.id || "");
                      }
                    }}
                  >
                    <option value="NATIONAL">National Newsroom (Main Slate)</option>
                    <option value="FANTASY">Mu Fantasy Desk</option>
                  </select>
                </label>

                <label>
                  <span className={label}>Target Team (Optional)</span>
                  <select
                    className={input}
                    value={formTeam}
                    onChange={e => setFormTeam(e.target.value)}
                  >
                    <option value="">League-Wide / None</option>
                    {customPlan.catalog.teams.map(t => (
                      <option key={t.id} value={String(t.id)}>
                        {t.city} {t.name} ({t.abbr})
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className={label}>Article Type</span>
                  <select
                    className={input}
                    value={formType}
                    onChange={e => {
                      const newType = e.target.value;
                      setFormType(newType);
                      const currentTypes = formDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types;
                      const found = currentTypes.find(t => t.id === newType);
                      if (found && found.allowed_writers.length > 0 && !found.allowed_writers.includes(formWriter)) {
                        setFormWriter(found.allowed_writers[0]);
                      }
                    }}
                  >
                    {(formDesk === "FANTASY" ? customPlan.catalog.fantasy_types : customPlan.catalog.national_types).map(t => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  <span className={label}>Writer / Persona</span>
                  <select
                    className={input}
                    value={formWriter}
                    onChange={e => setFormWriter(e.target.value)}
                  >
                    {(formDesk === "FANTASY" ? customPlan.catalog.fantasy_writers : customPlan.catalog.national_writers).map(w => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label>
                <span className={label}>Mandatory Angle / Desk Note</span>
                <textarea
                  className={`${input} min-h-[55px] resize-y font-sans`}
                  value={formDeskNote}
                  onChange={e => setFormDeskNote(e.target.value)}
                />
              </label>

              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  className={btnRed}
                  disabled={commissioning || busy || !token}
                  onClick={handleCommissionStory}
                >
                  {commissioning ? "Dispatching Swarm…" : "Step 2: Commission Story"}
                </button>
                <button
                  className={btn}
                  disabled={commissioning || busy}
                  onClick={() => setCustomPlan(null)}
                >
                  Edit Prompt
                </button>
              </div>
            </div>
          )}
        </section>

        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Runs</h3>
          <div className="grid gap-3">
            {RUNS.map(r => (
              <div key={r.kind} className="flex flex-col gap-1">
                <button className={btnRed} disabled={busy || !token || !!running} onClick={() => start(r.kind, r.title)}>{r.title}</button>
                <span className="text-xs text-[var(--gray-500)]">{r.note}</span>
              </div>
            ))}
          </div>
          <label className="block mt-4"><span className={label}>Resume a stopped run (optional)</span>
            <select className={input} value={resume} onChange={e => setResume(e.target.value)}>
              <option value="">— new run —</option>
              {resumable.map(c => <option key={c.folder} value={c.folder}>{c.folder} · {c.mode} · {c.stage}</option>)}
            </select>
          </label>
          {!token && <p className="text-xs text-[var(--red)] mt-2">Enter the debug token above to start runs or edit.</p>}
          {running && <p className="text-xs mt-2">A run is in progress; one at a time.</p>}
        </section>

        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Recent captures</h3>
          <div className="grid gap-2 text-xs">
            {(data?.captures || []).map(c => (
              <div key={c.id} className="flex flex-wrap gap-2 items-center border-b border-[var(--gray-100)] pb-1">
                <StatusPill status={c.status === "loaded" ? "done" : c.status === "failed" ? "failed" : c.status} />
                <b className="font-mono">{c.folder}</b><span>{c.mode}</span>{c.week !== null && <span>wk {c.week}</span>}
                {c.error && <span className="w-full text-[var(--red)] break-words">{c.error}</span>}
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="grid gap-4 content-start">
        <section className={card}>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <h3 className="font-black uppercase tracking-wide mr-auto">{latest ? `${latest.kind_label}` : "No runs yet"}</h3>
            {latest && <StatusPill status={latest.status} />}
            {latest && <span className="text-xs font-mono">{latest.started_at.replace("T", " ")}</span>}
            {running && <button className={btn} disabled={!token} onClick={() => stop(running.id)}>Stop</button>}
          </div>
          {latest?.status === "done" && latest.url_hash && (
            <div className="bg-emerald-50 border-2 border-emerald-500 p-3 mb-3 text-xs text-emerald-950 flex flex-wrap items-center justify-between gap-2">
              <div>
                <span className="font-black uppercase tracking-wider block text-[10px] text-emerald-800">
                  {latest.kind === "custom_commission" ? "Commissioned Story Published" : "Article Published"}
                </span>
                <span className="font-bold">{latest.headline || "Story ready"}</span>
              </div>
              <a
                href={latest.url_hash}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 bg-emerald-700 text-white font-bold uppercase text-[10px] tracking-wider hover:bg-emerald-800"
              >
                Read Story →
              </a>
            </div>
          )}
          <pre ref={logRef} className="bg-[#07080a] text-[#d8d8d8] text-[11px] leading-snug p-3 h-[420px] overflow-auto whitespace-pre-wrap break-words font-mono">{log || "Logs appear here when a run starts."}</pre>
        </section>
        <section className={card}>
          <h3 className="font-black uppercase tracking-wide mb-3">Run history</h3>
          <div className="grid gap-1 text-xs">
            {jobs.map(j => (
              <button key={j.id} className="flex flex-wrap gap-2 items-center text-left hover:bg-[var(--paper)] py-1"
                onClick={() => call<{ log: string }>(`/jobs/${j.id}/log?lines=300`).then(r => setLog(r.log))}>
                <StatusPill status={j.status} /><span className="font-mono">{j.id}</span><span>{j.kind_label}</span>
                {j.resume && <span className="text-[var(--gray-500)]">resume {j.resume}</span>}
                {j.exit_code !== undefined && j.exit_code !== null && <span className="text-[var(--gray-500)]">exit {j.exit_code}</span>}
              </button>
            ))}
            {!jobs.length && <span className="text-[var(--gray-500)]">None yet.</span>}
          </div>
        </section>
        {message && <p className="text-sm font-bold">{message}</p>}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- tables ----
function Tables({ token }: { token: string }) {
  const [tables, setTables] = useState<{ name: string; kind: string; rows: number | null }[]>([]);
  const [table, setTable] = useState(() => { try { return localStorage.getItem("mu_debug_table") || "league_state"; } catch { return "league_state"; } });
  const [data, setData] = useState<TableRows | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" } | null>(null);
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<{ row: number; col: string; value: string } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { call<typeof tables>("/tables").then(setTables).catch(e => setError(String(e.message))); }, []);

  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (sort) { params.set("sort", sort.col); params.set("dir", sort.dir); }
  if (q) params.set("q", q);
  const activeFilters = Object.fromEntries(Object.entries(filters).filter(([, v]) => v.trim() !== ""));
  if (Object.keys(activeFilters).length) params.set("filters", JSON.stringify(activeFilters));
  const query = params.toString();

  const load = useCallback(() => {
    setLoading(true);
    call<TableRows>(`/tables/${encodeURIComponent(table)}?${query}`)
      .then(d => { setData(d); setError(""); })
      .catch(e => setError(String(e.message)))
      .finally(() => setLoading(false));
  }, [table, query]);

  useEffect(() => {
    const t = window.setTimeout(load, 250);           // debounce typing in the search and filter boxes
    return () => window.clearTimeout(t);
  }, [load]);

  const pick = (name: string) => {
    setTable(name); setPage(1); setSort(null); setQ(""); setFilters({}); setEditing(null); setData(null);
    try { localStorage.setItem("mu_debug_table", name); } catch { /* ignore */ }
  };

  const editable = (c: Column) => !!data && data.kind === "table" && data.pk.length > 0 && !c.generated && !c.pk && !!token;

  const save = async () => {
    if (!data || !editing) return;
    const row = data.rows[editing.row];
    const key = Object.fromEntries(data.pk.map(k => [k, row[k]]));
    const value = editing.value === "" ? null : editing.value;
    try {
      const r = await call<{ row: Record<string, unknown> }>(`/tables/${encodeURIComponent(data.table)}`,
        { method: "PATCH", body: JSON.stringify({ key, changes: { [editing.col]: value } }) }, token);
      setData({ ...data, rows: data.rows.map((x, i) => (i === editing.row ? r.row : x)) });
      setEditing(null); setError("");
    } catch (e) { setError(`Not saved: ${(e as Error).message}`); }
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="min-w-[220px]"><span className={label}>Table</span>
          <select className={input} value={table} onChange={e => pick(e.target.value)}>
            {tables.map(t => <option key={t.name} value={t.name}>{t.name}{t.kind === "view" ? " (view)" : ""}{t.rows !== null ? ` · ~${t.rows.toLocaleString()}` : ""}</option>)}
          </select>
        </label>
        <label className="flex-1 min-w-[180px]"><span className={label}>Search all columns</span>
          <input className={input} value={q} onChange={e => { setQ(e.target.value); setPage(1); }} placeholder="text…" />
        </label>
        <label><span className={label}>Rows</span>
          <select className={input} value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}>
            {[25, 50, 100, 200].map(n => <option key={n}>{n}</option>)}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <button className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button>
          <span className="text-xs font-mono whitespace-nowrap">{page} / {pages}{data ? ` · ${data.total.toLocaleString()} rows` : ""}</span>
          <button className={btn} disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button>
        </div>
      </div>
      <p className="text-[11px] text-[var(--gray-500)]">
        Click a header to sort. Filter boxes: text = contains; <code>=5</code>, <code>!=5</code>, <code>&gt;=300</code>, <code>&lt;10</code> compare; <code>null</code> / <code>!null</code>.
        {data && data.kind === "table" && data.pk.length > 0 ? (token ? " Tap a cell to edit (Enter saves, Esc cancels, empty = NULL)." : " Enter the debug token to edit cells.") : data ? " Views and tables without a primary key are read-only." : ""}
      </p>
      {error && <p className="text-sm text-[var(--red)] font-bold">{error}</p>}
      <div className={`overflow-auto max-h-[70vh] bg-white border-t-4 border-[var(--ink)] ${loading ? "opacity-60" : ""}`}>
        {data && (
          <table className="text-xs border-collapse min-w-full">
            <thead className="sticky top-0 bg-[var(--gray-100)] z-10">
              <tr>
                {data.columns.map(c => (
                  <th key={c.column} className="text-left px-2 py-1 font-bold whitespace-nowrap cursor-pointer select-none border-b border-[var(--gray-300)]"
                    title={`${c.type}${c.pk ? " · primary key" : ""}${c.generated ? " · generated" : ""}`}
                    onClick={() => { setSort(sort?.col === c.column ? { col: c.column, dir: sort.dir === "asc" ? "desc" : "asc" } : { col: c.column, dir: "asc" }); setPage(1); }}>
                    {c.column}{c.pk ? " 🔑" : ""}{sort?.col === c.column ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
              <tr>
                {data.columns.map(c => (
                  <th key={c.column} className="px-1 py-1 border-b border-[var(--gray-300)]">
                    <input className="w-full min-w-[64px] border border-[var(--gray-300)] px-1 py-0.5 text-[11px] font-normal bg-white"
                      value={filters[c.column] || ""} placeholder="filter"
                      onChange={e => { setFilters({ ...filters, [c.column]: e.target.value }); setPage(1); }} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="odd:bg-[var(--paper)]">
                  {data.columns.map(c => {
                    const isEditing = editing?.row === i && editing.col === c.column;
                    const v = row[c.column];
                    return (
                      <td key={c.column} className={`px-2 py-1 align-top border-b border-[var(--gray-100)] ${editable(c) ? "cursor-text hover:bg-amber-50" : ""}`}
                        onClick={() => { if (editable(c) && !isEditing) setEditing({ row: i, col: c.column, value: show(v) }); }}>
                        {isEditing ? (
                          <input autoFocus className="border border-[var(--red)] px-1 py-0.5 text-xs w-full min-w-[120px]"
                            value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })}
                            onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(null); }}
                            onBlur={() => setEditing(null)} />
                        ) : v === null || v === undefined ? (
                          <span className="text-[var(--gray-300)]">∅</span>
                        ) : (
                          <span className="block max-w-[360px] truncate" title={show(v)}>{show(v)}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------- endpoints ----
function Endpoints() {
  const [eps, setEps] = useState<{ path: string; methods: string[]; doc: string; path_params: string[]; query_params: string[] }[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { call<typeof eps>("/endpoints").then(setEps).catch(() => setEps([])); }, []);
  const shown = eps.filter(e => !q || e.path.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="grid gap-3">
      <input className={`${input} max-w-sm`} placeholder="Filter paths…" value={q} onChange={e => setQ(e.target.value)} />
      <div className="bg-white border-t-4 border-[var(--ink)]">
        {shown.map(e => (
          <div key={e.path + e.methods.join()} className="flex flex-wrap gap-x-3 gap-y-1 items-baseline px-3 py-2 border-b border-[var(--gray-100)] text-sm">
            {e.methods.map(m => <span key={m} className={`text-[10px] font-bold px-1.5 py-0.5 ${m === "GET" ? "bg-emerald-600" : m === "DELETE" ? "bg-[var(--red)]" : "bg-[var(--ink)]"} text-white`}>{m}</span>)}
            {e.methods.includes("GET") && !e.path_params.length
              ? <a className="font-mono underline" href={`${API_BASE}${e.path}`} target="_blank" rel="noreferrer">{e.path}</a>
              : <span className="font-mono">{e.path}</span>}
            {e.query_params.length > 0 && <span className="text-xs text-[var(--gray-500)]">?{e.query_params.join("&")}</span>}
            {e.doc && <span className="w-full text-xs text-[var(--gray-500)]">{e.doc}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- docs ----
function inline(text: string): ReactNode[] {
  // `code`, **bold**, *italic*; links show their text
  return text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) return <code key={i} className="font-mono text-[0.85em] bg-[var(--gray-100)] px-1">{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) return <b key={i}>{part.slice(2, -2)}</b>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <i key={i}>{part.slice(1, -1)}</i>;
    return part;
  });
}

function DocMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  const sizes = ["text-3xl", "text-2xl", "text-xl", "text-base", "text-sm", "text-sm"];
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(<pre key={out.length} className="bg-[#07080a] text-[#d8d8d8] text-[11px] leading-snug p-3 overflow-x-auto my-3 font-mono">{code.join("\n")}</pre>);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      out.push(<div key={out.length} className={`${sizes[h[1].length - 1]} font-black uppercase tracking-wide mt-6 mb-2 ${h[1].length <= 2 ? "border-b-2 border-[var(--ink)] pb-1" : ""}`}>{inline(h[2])}</div>);
      i++; continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|"));
        if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push(
        <div key={out.length} className="overflow-x-auto my-3">
          <table className="text-xs border-collapse">
            <tbody>{rows.map((r, ri) => <tr key={ri} className={ri === 0 ? "bg-[var(--gray-100)] font-bold" : "border-t border-[var(--gray-100)]"}>{r.map((c, ci) => <td key={ci} className="px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
      continue;
    }
    const li = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (li) {
      out.push(<div key={out.length} className="text-sm leading-relaxed flex gap-2" style={{ paddingLeft: `${li[1].length * 8}px` }}><span className="text-[var(--gray-500)]">{/\d/.test(li[2]) ? li[2] : "•"}</span><span>{inline(li[3])}</span></div>);
      i++; continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#|```|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(<p key={out.length} className="text-sm leading-relaxed my-2">{inline(para.join(" "))}</p>);
  }
  return <>{out}</>;
}

const DOCS: { key: string; title: string }[] = [
  { key: "spec", title: "Project spec" },
  { key: "migration", title: "Pipeline migration" },
  { key: "frontend", title: "Frontend spec" },
];

function Docs() {
  const [doc, setDoc] = useState("spec");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    setText("");
    call<{ markdown: string }>(`/docs/${doc}`).then(r => { setText(r.markdown); setError(""); }).catch(e => setError(String(e.message)));
  }, [doc]);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        {DOCS.map(d => <button key={d.key} className={`${btn} ${doc === d.key ? "!bg-[var(--ink)] !text-white" : ""}`} onClick={() => setDoc(d.key)}>{d.title}</button>)}
      </div>
      {error && <p className="text-sm text-[var(--red)]">{error}</p>}
      <article className="bg-white border-t-4 border-[var(--ink)] p-4 md:p-8 overflow-x-auto debug-doc">
        {text ? <DocMarkdown content={text} /> : !error && <p className="text-sm text-[var(--gray-500)]">Loading…</p>}
      </article>
    </div>
  );
}

// ---------------------------------------------------------------- page ----
const TABS = ["Control", "Tables", "Endpoints", "Docs"] as const;

export default function DebugPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>(() => {
    try { return (localStorage.getItem("mu_debug_tab") as (typeof TABS)[number]) || "Control"; } catch { return "Control"; }
  });
  const [token, setToken] = useState(readToken);
  const [data, setData] = useState<DebugState | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    call<DebugState>("/state").then(d => { setData(d); setError(""); }).catch(e => setError(String(e.message || e)));
  }, []);
  useEffect(() => { reload(); const t = window.setInterval(reload, 10000); return () => window.clearInterval(t); }, [reload]);

  const choose = (t: (typeof TABS)[number]) => { setTab(t); try { localStorage.setItem("mu_debug_tab", t); } catch { /* ignore */ } };
  const saveToken = (v: string) => { setToken(v); try { localStorage.setItem(TOKEN_KEY, v); } catch { /* ignore */ } };

  return (
    <main className="max-w-[1400px] mx-auto px-4 py-6 md:py-10">
      <div className="flex flex-wrap items-end gap-4 border-b-2 border-[var(--ink)] pb-4 mb-4">
        <div className="mr-auto">
          <p className="text-[10px] font-bold uppercase tracking-[.2em] text-[var(--red)]">Debug</p>
          <h1 className="text-3xl md:text-4xl font-black uppercase leading-none">League control</h1>
          {data && (
            <p className="text-xs font-mono mt-2">
              {data.database} · season {data.state.current_season} · current week {data.state.current_week}
              {data.running_job ? ` · running: ${data.running_job.kind}` : ""}
            </p>
          )}
          {error && <p className="text-xs text-[var(--red)] mt-2">API not reachable ({error}). Is it running on {API_BASE}?</p>}
        </div>
        <label className="w-full sm:w-64"><span className={label}>Debug token {token ? "✓" : "(needed for edits and runs)"}</span>
          <input className={input} type="password" value={token} onChange={e => saveToken(e.target.value)} placeholder="DEBUG_TOKEN from .env" />
        </label>
      </div>
      <nav className="flex gap-1 mb-4 overflow-x-auto">
        {TABS.map(t => (
          <button key={t} onClick={() => choose(t)}
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider border-b-4 ${tab === t ? "border-[var(--red)]" : "border-transparent text-[var(--gray-500)]"}`}>{t}</button>
        ))}
      </nav>
      {tab === "Control" && <Control token={token} data={data} reload={reload} />}
      {tab === "Tables" && <Tables token={token} />}
      {tab === "Endpoints" && <Endpoints />}
      {tab === "Docs" && <Docs />}
    </main>
  );
}
