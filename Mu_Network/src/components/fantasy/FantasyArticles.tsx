import { useContext, useEffect, useState, type ReactNode } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { FantasyHeader } from "./FantasyHeader";
import { fantasyArticles } from "./FantasyPlayerContext";

export const fantasyInitials = (name: string) => (name || "").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "MU";

export const fantasyDate = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).toUpperCase();
};

// Inline markdown: **bold**, *italic*. Built as React nodes (no HTML injection).
function fantasyInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(m[1] !== undefined ? <strong key={m.index}>{m[1]}</strong> : <em key={m.index}>{m[2]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Article body from the newsroom's markdown: headings, lists, tables, quotes and rules. The H1 and the
// EXCERPT line are shown in the header, so they're skipped here.
export function FantasyMarkdown({ content }: { content: string }) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let firstParagraph = true;
  const flush = () => {
    if (!para.length) return;
    const text = para.join(" ").trim();
    para = [];
    if (!text) return;
    blocks.push(<p key={blocks.length} className={firstParagraph ? "fantasy-article-lead" : undefined}>{fantasyInline(text)}</p>);
    firstParagraph = false;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) { flush(); continue; }
    if (/^#\s/.test(line) || /^>\s*\*\*EXCERPT:\*\*/i.test(line)) { flush(); continue; }
    if (/^(-{3,}|\*{3,})$/.test(line)) { flush(); blocks.push(<hr key={blocks.length} />); continue; }
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      blocks.push(level === 2 ? <h2 key={blocks.length}>{fantasyInline(heading[2])}</h2> : <h3 key={blocks.length}>{fantasyInline(heading[2])}</h3>);
      continue;
    }
    if (line.startsWith(">")) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) { quote.push(lines[i].trim().replace(/^>\s?/, "")); i++; }
      i--;
      blocks.push(<blockquote key={blocks.length}>{fantasyInline(quote.join(" "))}</blockquote>);
      continue;
    }
    if (line.startsWith("|")) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const [head, ...body] = rows;
      blocks.push(
        <div className="fantasy-article-table" key={blocks.length}>
          <table>
            {head && <thead><tr>{head.map((c, k) => <th key={k}>{fantasyInline(c)}</th>)}</tr></thead>}
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{fantasyInline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
      continue;
    }
    const isBullet = /^[-*•]\s+/.test(line);
    const isNumber = /^\d+[.)]\s+/.test(line);
    if (isBullet || isNumber) {
      flush();
      const items: string[] = [];
      const itemRe = isBullet ? /^[-*•]\s+/ : /^\d+[.)]\s+/;
      while (i < lines.length && itemRe.test(lines[i].trim())) { items.push(lines[i].trim().replace(itemRe, "")); i++; }
      i--;
      const children = items.map((it, k) => <li key={k}>{fantasyInline(it)}</li>);
      blocks.push(isNumber ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
      continue;
    }
    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}

export function FantasyArticlePage({ slug }: { slug: string }) {
  const systemStatus = useContext(SystemContext);
  const isStored = /^\d+$/.test(slug);  // stored newsroom article id; placeholders are 'start-sit' / 'p0'..'p2'
  const [stored, setStored] = useState<any>(null);
  const [weekArticles, setWeekArticles] = useState<any>(null);
  const [dashboardData, setDashboardData] = useState<any>(null);

  useEffect(() => {
    if (!isStored) return;
    fetch(`${API_BASE}/api/news/article/${slug}`)
      .then((res) => res.json())
      .then((data) => { if (!data.error) setStored(data); })
      .catch(() => {});
  }, [slug, isStored]);

  useEffect(() => {
    const week = stored?.week ?? systemStatus.week;
    fetch(`${API_BASE}/api/fantasy/articles?season=${systemStatus.season}&week=${week}`)
      .then((res) => res.json())
      .then(setWeekArticles)
      .catch(() => {});
  }, [stored?.week, systemStatus.season, systemStatus.week]);

  useEffect(() => {
    if (isStored) return;
    fetch(`${API_BASE}/api/fantasy/dashboard?season=${systemStatus.season}&week=${systemStatus.week}`)
      .then((res) => res.json())
      .then(setDashboardData)
      .catch(() => {});
  }, [isStored, systemStatus.season, systemStatus.week]);

  const siblings: any[] = [...(weekArticles?.front_page || []), ...(weekArticles?.more_analysis || [])];
  const card = siblings.find((a) => String(a.id) === slug);

  // Placeholder articles (no stored slate yet) keep the old generic copy.
  const isHero = slug === "start-sit";
  const placeholderIndex = Number(slug.replace(/^p/, "")) || 0;
  const placeholder = isHero ? { ...(dashboardData?.hero || {}) } : { ...(fantasyArticles[placeholderIndex] || fantasyArticles[0]), ...(dashboardData?.articles?.[placeholderIndex] || {}) };

  const label = card?.label || (stored?.article_type ? stored.article_type.replace(/^FANTASY_/, "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c: string) => c.toUpperCase()) : placeholder.label) || "Fantasy Analysis";
  const title = stored?.headline || placeholder.title || "";
  const excerpt = stored?.excerpt || placeholder.excerpt || "";
  const author = card?.author || stored?.author?.display_name || placeholder.author || "Mu Fantasy Desk";
  const readTime = card?.time ? `${card.time} read` : placeholder.time || "";
  const week = stored?.week ?? systemStatus.week;
  const others = siblings.filter((a) => String(a.id) !== slug).slice(0, 5);

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="news" />
      <main className="fantasy-article-page">
        <div className="fantasy-article-crumb"><a href="#fantasy">News &amp; Articles</a><span>/</span><a href="#fantasy-all">All analysis</a><span>/</span><b>{label}</b><span className="fantasy-article-week">SEASON {systemStatus.season} · WEEK {week}</span></div>
        <article className="fantasy-article-panel">
          <header className="fantasy-article-heading">
            <div className="fantasy-article-kicker"><span>{label}</span>{card?.updated && <b>{fantasyDate(card.updated)}</b>}</div>
            <h1>{title}</h1>
            {excerpt && <p>{excerpt}</p>}
            <div className="fantasy-article-byline"><span>{fantasyInitials(author)}</span><div><strong>By {author}</strong><small>MU FANTASY ANALYTICS{readTime ? ` · ${readTime.toUpperCase()}` : ""}</small></div><a href="#fantasy-rankings">Open rankings →</a></div>
          </header>
          <div className="fantasy-article-content">
            <div className="fantasy-article-copy">
              {isStored ? (
                stored ? <FantasyMarkdown content={stored.content} /> : <p>Loading article…</p>
              ) : (
                <>
                  <p className="fantasy-article-lead">{excerpt || title}</p>
                  <p>This week's fantasy analysis will appear here once the Mu Fantasy desk publishes its slate.</p>
                </>
              )}
              <a className="fantasy-article-rankings-link" href="#fantasy-rankings">Explore this week's rankings <span>→</span></a>
            </div>
            <aside className="fantasy-article-rail">
              <span>WEEK {week} · PPR</span>
              <strong>More from the fantasy desk</strong>
              {others.length ? (
                others.map((a) => <a className="fantasy-article-rail-link" href={`#article-fantasy-${a.id}`} key={a.id}><small>{a.label}</small>{a.title}</a>)
              ) : (
                <p>Use role, matchup and projection together. No single number tells the whole story.</p>
              )}
              <a href="#fantasy-all">All analysis this week →</a>
            </aside>
          </div>
        </article>
      </main>
    </div>
  );
}

export function FantasyAllPage() {
  const systemStatus = useContext(SystemContext);
  const [requestedWeek, setRequestedWeek] = useState<number>(systemStatus.week);
  const [data, setData] = useState<any>(null);

  useEffect(() => { setRequestedWeek(systemStatus.week); }, [systemStatus.week]);
  useEffect(() => {
    fetch(`${API_BASE}/api/fantasy/articles?season=${systemStatus.season}&week=${requestedWeek}`)
      .then((res) => res.json())
      .then(setData)
      .catch(() => {});
  }, [systemStatus.season, requestedWeek]);

  const sections: [string, any[]][] = [["Front page", data?.front_page || []], ["More analysis", data?.more_analysis || []]];
  const weeks: number[] = data?.available_weeks || [];

  return (
    <div className="fantasy-shell">
      <FantasyHeader active="news" />
      <main className="fantasy-article-page">
        <div className="fantasy-article-crumb"><a href="#fantasy">News &amp; Articles</a><span>/</span><b>All analysis</b><span className="fantasy-article-week">SEASON {systemStatus.season}{data?.week ? ` · WEEK ${data.week}` : ""}</span></div>
        <section className="fantasy-all">
          <div className="fantasy-section-heading">
            <div><span>THE FULL DESK</span><h1>All fantasy analysis</h1></div>
            <p>Every piece from the Mu Fantasy desk for the week, front page first.</p>
          </div>
          {weeks.length > 1 && (
            <div className="fantasy-all-weeks" role="tablist" aria-label="Fantasy week">
              {weeks.map((w) => (
                <button key={w} className={w === data?.week ? "active" : ""} onClick={() => setRequestedWeek(w)}>W{w}</button>
              ))}
            </div>
          )}
          {!data ? (
            <p className="fantasy-all-empty">Loading…</p>
          ) : !data.week ? (
            <p className="fantasy-all-empty">The fantasy desk hasn't published a slate yet this season.</p>
          ) : (
            sections.filter(([, items]) => items.length).map(([name, items]) => (
              <div className="fantasy-all-section" key={name}>
                <div className="fantasy-subhead"><div><span>{String(items.length).padStart(2, "0")}</span><h2>{name}</h2></div></div>
                <div className="fantasy-all-list">
                  {items.map((a) => (
                    <a className="fantasy-all-item" href={`#article-fantasy-${a.id}`} key={a.id}>
                      <span>{a.label}</span>
                      <h3>{a.title}</h3>
                      {a.excerpt && <p>{a.excerpt}</p>}
                      <small>BY {String(a.author || "").toUpperCase()} <b>•</b> {String(a.time || "").toUpperCase()} READ</small>
                    </a>
                  ))}
                </div>
              </div>
            ))
          )}
        </section>
      </main>
    </div>
  );
}
