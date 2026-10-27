import { useContext } from "react";
import { SystemContext } from "../../context/SystemContext";
import { localStories, logo, stories } from "../../lib/gridiron";
import type { PowerRankingsResponse } from "./PowerRankingsPage";

interface NewsroomHomePageProps {
  week: number;
  setWeek: (week: number) => void;
  newsData: any;
  hasOffseasonArticles: boolean;
  powerRankings: PowerRankingsResponse | null;
  showAllStories: boolean;
  setShowAllStories: (show: boolean) => void;
}

export function NewsroomHomePage({
  week,
  setWeek,
  newsData,
  hasOffseasonArticles,
  powerRankings,
  showAllStories,
  setShowAllStories,
}: NewsroomHomePageProps) {
  const systemStatus = useContext(SystemContext);
  const canAccessOffseason = hasOffseasonArticles || week === 24 || (systemStatus.week && systemStatus.week >= 24);
  const maxNewsWeek = canAccessOffseason ? Math.max(systemStatus.week || 24, 24) : Math.max(systemStatus.week || 19, 19);

  return (
    <main>
      <div className="edition-bar">
        <span>{week === 24 ? "OFFSEASON EDITION" : "THE NEWSROOM"}</span>
        <span>{systemStatus.season} SEASON</span>
        <div className="edition-week-picker">
          <button
            className="edition-week-btn"
            onClick={() => setWeek(Math.max(1, week - 1))}
            disabled={week <= 1}
            aria-label="Previous week"
            title="Previous week"
          >
            ◀
          </button>
          <select
            className="edition-week-select"
            value={week}
            onChange={(e) => setWeek(Number(e.target.value))}
            aria-label="Select news week"
          >
            {Array.from({ length: maxNewsWeek }, (_, i) => i + 1).map((w) => (
              <option key={w} value={w}>
                WEEK {w}{w === 19 ? " · WILD CARD" : w === 20 ? " · DIVISIONAL" : w === 21 ? " · CONF CHAMP" : w === 22 ? " · PRO BOWL" : w === 23 ? " · SUPER BOWL" : w === 24 ? " · OFFSEASON" : ""}
              </option>
            ))}
          </select>
          <button
            className="edition-week-btn"
            onClick={() => setWeek(Math.min(maxNewsWeek, week + 1))}
            disabled={week >= maxNewsWeek}
            aria-label="Next week"
            title="Next week"
          >
            ▶
          </button>
        </div>
      </div>

      <div className="page-grid" style={week === 24 ? { gridTemplateColumns: "1fr" } : undefined}>
        <section className="main-column">
          <div className="section-heading">
            <div><span className="red-rule" /><h1>National Desk</h1></div>
            <p>{week === 24 ? "Offseason reporting, analysis, and editorial package" : `Week ${week} reporting, analysis, and film study`}</p>
          </div>

          {/* HERO STORY */}
          {(() => {
            const hero = newsData?.slotted?.hero;
            const heroId = hero?.id ?? "game-of-week";
            const heroTitle = hero?.headline || (week === 24 ? "The Super Bowl: How the Game Was Won" : "One night. Two contenders. Everything on the line.");
            const heroExcerpt = hero?.excerpt || (week === 24 ? "Inside the pivotal fourth-quarter swings and championship-clinching adjustments that decided the title." : "The league's most explosive offense meets the defense built to stop it. Inside the tactical battle that will define the season.");
            const heroAuthor = hero?.author?.display_name || "Bill Barnwell";
            const heroInitials = heroAuthor.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "BB";
            const heroRole = hero?.author?.archetype ? hero.author.archetype.replace(/_/g, " ") : "National Writer";
            const heroSlotLabel = hero?.slot ? hero.slot.toUpperCase() : (week === 24 ? "OFFSEASON LEAD · SUPER BOWL RECAP" : "GAME OF THE WEEK");

            return (
              <article className="hero-story">
                <a href={`#article-${heroId}`} className="hero-image">
                  <img
                    src="https://images.unsplash.com/photo-1634306320557-4c4e4660692f?auto=format&fit=crop&w=1800&q=88"
                    alt="Football player celebrating under stadium lights"
                  />
                  <div className="hero-shade" />
                  <span className="hero-label">{heroSlotLabel}</span>
                  <span className="play-button" aria-hidden="true"><i /></span>
                </a>
                <div className="hero-content">
                  <div className="hero-meta"><span>{week === 24 ? "OFFSEASON LEAD READ" : "THE BIG READ"}</span><span>12 MIN READ</span></div>
                  <a href={`#article-${heroId}`}><h2>{heroTitle}</h2></a>
                  <p>{heroExcerpt}</p>
                  <div className="byline">
                    <span className="avatar red">{heroInitials}</span>
                    <span><small>DEEP DIVE BY</small><b>{heroAuthor}</b><em>{heroRole}</em></span>
                  </div>
                </div>
              </article>
            );
          })()}

          {/* ALL-TIME NFL RECORD SPOTLIGHT */}
          {(() => {
            const records = newsData?.slotted?.historic_events;
            if (!Array.isArray(records) || records.length === 0) return null;
            return (
              <section className="historic-records-spotlight" aria-label="Historic NFL Record Breaking Coverage">
                <div className="historic-records-header">
                  <span className="trophy-badge">🏆 ALL-TIME NFL RECORD BROKEN</span>
                  <span className="historic-subtitle">HISTORY WRITTEN ON THE GRIDIRON</span>
                </div>
                <div className="historic-records-grid">
                  {records.map((story: any) => {
                    const author = story.author?.display_name || "National Correspondent";
                    const initials = author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase();
                    return (
                      <article key={story.id} className="historic-record-spotlight-card">
                        <div className="historic-card-top">
                          <span className="record-slot-chip">{story.slot || "Historic Milestone"}</span>
                          <span className="record-exclusive-tag">COMMEMORATIVE FEATURE</span>
                        </div>
                        <a href={`#article-${story.id}`}>
                          <h3>{story.headline}</h3>
                        </a>
                        <p>{story.excerpt}</p>
                        <div className="author-row">
                          <span className="avatar amber">{initials}</span>
                          <span><b>{author}</b><small>HISTORICAL ANALYSIS · 9 MIN READ</small></span>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })()}

          {week === 24 ? (
            <div className="offseason-editorial-slate">
              {/* 2 SECONDARY STORIES */}
              <div className="latest-heading">
                <h2>Secondary Slots (1 &amp; 2) · Retrospective &amp; Loss Analysis</h2>
                <span style={{ fontSize: "11px", fontWeight: "bold", textTransform: "uppercase", letterSpacing: ".08em", color: "var(--gray-500)", fontFamily: "var(--font-mono)" }}>
                  Secondary Analysis · Slots 1 &amp; 2
                </span>
              </div>
              <div className="story-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)" }}>
                {(() => {
                  const secondary = (newsData?.slotted?.secondary && newsData.slotted.secondary.length > 0)
                    ? newsData.slotted.secondary
                    : [];

                  if (secondary.length === 0) {
                    return (
                      <div style={{ gridColumn: "1 / -1", padding: "28px", color: "var(--gray-500)", background: "var(--paper)", border: "1px solid var(--gray-300)" }}>
                        No secondary retrospective articles loaded for Week 24 yet.
                      </div>
                    );
                  }

                  return secondary.map((story: any, idx: number) => {
                    const isDb = Boolean(story.id);
                    const artLink = isDb ? `#article-${story.id}` : `#article-offseason-sec-${idx}`;
                    const title = story.headline || story.title;
                    const blurb = story.excerpt || story.blurb;
                    const author = story.author?.display_name || story.author || "National Reporter";
                    const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                    const kicker = story.article_type
                      ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                      : (story.slot || "Secondary Analysis");
                    const readTime = story.read || "8 min read";

                    return (
                      <article className="story-card" key={story.id || title} style={{ borderLeft: idx % 2 === 1 ? "1px solid var(--gray-300)" : undefined }}>
                        <span className="story-number">0{idx + 1}</span>
                        <div className="story-copy">
                          <span className="kicker">{kicker}</span>
                          <a href={artLink}><h3>{title}</h3></a>
                          <p>{blurb}</p>
                          <div className="author-row">
                            <span className="avatar">{initials}</span>
                            <span><b>{author}</b><small>NATIONAL REPORTER · {readTime}</small></span>
                          </div>
                        </div>
                      </article>
                    );
                  });
                })()}
              </div>

              {/* 3 FEATURE STORIES */}
              <div className="latest-heading" style={{ marginTop: "44px" }}>
                <h2>Feature Slots (3, 4 &amp; 5) · Offseason In-Depth Audits</h2>
                <span style={{ fontSize: "11px", fontWeight: "bold", textTransform: "uppercase", letterSpacing: ".08em", color: "var(--gray-500)", fontFamily: "var(--font-mono)" }}>
                  Coaching · Free Agency · Draft · Slots 3, 4 &amp; 5
                </span>
              </div>
              <div
                className="offseason-features-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  borderBottom: "1px solid var(--gray-300)"
                }}
              >
                {(() => {
                  const features = (newsData?.slotted?.features && newsData.slotted.features.length > 0)
                    ? newsData.slotted.features
                    : [];

                  if (features.length === 0) {
                    return (
                      <div style={{ gridColumn: "1 / -1", padding: "28px", color: "var(--gray-500)", background: "var(--paper)", border: "1px solid var(--gray-300)" }}>
                        No offseason feature articles loaded for Week 24 yet.
                      </div>
                    );
                  }

                  return features.map((story: any, idx: number) => {
                    const isDb = Boolean(story.id);
                    const artLink = isDb ? `#article-${story.id}` : `#article-offseason-feat-${idx}`;
                    const title = story.headline || story.title;
                    const blurb = story.excerpt || story.blurb;
                    const author = story.author?.display_name || story.author || "National Columnist";
                    const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                    const kicker = story.article_type
                      ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                      : (story.slot || "Feature Audit");
                    const readTime = story.read || "9 min read";

                    return (
                      <article
                        className="story-card"
                        key={story.id || title}
                        style={{
                          borderLeft: idx > 0 ? "1px solid var(--gray-300)" : undefined,
                          borderBottom: "none"
                        }}
                      >
                        <span className="story-number">0{idx + 3}</span>
                        <div className="story-copy">
                          <span className="kicker">{kicker}</span>
                          <a href={artLink}><h3 style={{ fontSize: "20px" }}>{title}</h3></a>
                          <p>{blurb}</p>
                          <div className="author-row">
                            <span className="avatar amber">{initials}</span>
                            <span><b>{author}</b><small>EXPERT ANALYST · {readTime}</small></span>
                          </div>
                        </div>
                      </article>
                    );
                  });
                })()}
              </div>

              {/* OTHER ARTICLES (IF ANY) */}
              {Array.isArray(newsData?.slotted?.other) && newsData.slotted.other.length > 0 && (
                <>
                  <div className="latest-heading" style={{ marginTop: "44px" }}>
                    <h2>Additional Coverage</h2>
                  </div>
                  <div className="story-grid">
                    {newsData.slotted.other.map((story: any, idx: number) => {
                      const isDb = Boolean(story.id);
                      const artLink = isDb ? `#article-${story.id}` : `#article-other-${idx}`;
                      const title = story.headline || story.title;
                      const blurb = story.excerpt || story.blurb;
                      const author = story.author?.display_name || story.author || "Contributor";
                      const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : "MU";
                      const kicker = story.article_type
                        ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                        : (story.slot || "Special Commission");

                      return (
                        <article className="story-card" key={story.id || title}>
                          <span className="story-number">0{idx + 6}</span>
                          <div className="story-copy">
                            <span className="kicker">{kicker}</span>
                            <a href={artLink}><h3>{title}</h3></a>
                            <p>{blurb}</p>
                            <div className="author-row">
                              <span className="avatar">{initials}</span>
                              <span><b>{author}</b><small>CONTRIBUTOR · 7 min read</small></span>
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          ) : (
            <>
              <div className="latest-heading">
                <h2>{showAllStories ? "This Week's Stories" : "Latest Analysis"}</h2>
                <button onClick={() => setShowAllStories(!showAllStories)}>
                  {showAllStories ? "Show featured" : "See all stories"} <span>{showAllStories ? "↑" : "→"}</span>
                </button>
              </div>
              <div className={`story-grid ${showAllStories ? "expanded" : ""}`}>
                {(() => {
                  const slotted = newsData?.slotted;
                  const dynamicStories: any[] = [];
                  if (slotted) {
                    if (Array.isArray(slotted.secondary)) dynamicStories.push(...slotted.secondary);
                    if (Array.isArray(slotted.features)) dynamicStories.push(...slotted.features);
                    if (Array.isArray(slotted.other)) dynamicStories.push(...slotted.other);
                  }

                  const storyList = dynamicStories.length > 0
                    ? (showAllStories ? dynamicStories : dynamicStories.slice(0, 3))
                    : (showAllStories ? stories : stories.slice(0, 3));

                  return storyList.map((story: any, index: number) => {
                    const isDb = Boolean(story.id);
                    const artLink = isDb ? `#article-${story.id}` : `#article-national-${index}`;
                    const title = story.headline || story.title;
                    const blurb = story.excerpt || story.blurb;
                    const author = story.author?.display_name || story.author || "National Reporter";
                    const initials = isDb ? author.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() : story.initials;
                    const isRecord = story.article_type === "HISTORIC_RECORD_BREAK" || (story.slot && story.slot.toLowerCase().includes("record"));
                    const kicker = isRecord
                      ? "🏆 ALL-TIME NFL RECORD"
                      : story.article_type
                        ? story.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase())
                        : (story.kicker || "Analysis");
                    const readTime = story.read || "7 min read";

                    return (
                      <article className={`story-card ${index === 0 ? "feature-card" : ""} ${isRecord ? "record-breaker-card" : ""}`} key={story.id || title}>
                        <span className="story-number">0{index + 1}</span>
                        <div className="story-copy">
                          <span className="kicker">{kicker}</span>
                          <a href={artLink}><h3>{title}</h3></a>
                          <p>{blurb}</p>
                          <div className="author-row">
                            <span className={`avatar ${isRecord ? "amber" : ""}`}>{initials}</span>
                            <span><b>{author}</b><small>NATIONAL REPORTER · {readTime}</small></span>
                          </div>
                        </div>
                      </article>
                    );
                  });
                })()}
              </div>
            </>
          )}
        </section>

        {week !== 24 && (
          <aside className="sidebar">
            <div className="sticky-panel">
              <div className="sidebar-title">
                <span className="pulse" />
                <div><span>POPULAR MARKETS</span><h2>Trending Local Coverage</h2></div>
              </div>
              <div className="market-list">
                {(() => {
                  const primers = newsData?.slotted?.local_primers;
                  if (Array.isArray(primers) && primers.length > 0) {
                    return primers.map((story: any, index: number) => {
                      // Label by the team the primer is filed under; the opponent is often mentioned more
                      const primaryTeam = story.tagged_teams?.find((t: any) => t.id === story.team_id)
                        || story.tagged_teams?.find((t: any) => t.mention_type === 'PRIMARY') || story.tagged_teams?.[0];
                      const teamAbbr = primaryTeam?.abbr?.toLowerCase() || "nfl";
                      const teamName = primaryTeam?.name || "Local";
                      const author = story.author?.display_name || "Franchise Beat";

                      return (
                        <article className="market-story" key={story.id}>
                          <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                          <div className="market-copy">
                            <span className="team-tag"><img src={logo(teamAbbr)} alt="" />{teamName} Beat</span>
                            <a href={`#article-${story.id}`}><h3>{story.headline}</h3></a>
                            <p>By {author} <span>·</span> Beat Reporter</p>
                          </div>
                        </article>
                      );
                    });
                  }

                  return localStories.map((story, index) => (
                    <article className="market-story" key={story.title}>
                      <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                      <div className="market-copy">
                        <span className="team-tag"><img src={logo(story.team)} alt="" />{story.tag}</span>
                        <a href={`#article-local-${story.team}`}><h3>{story.title}</h3></a>
                        <p>By {story.author} <span>·</span> {story.time} ago</p>
                      </div>
                    </article>
                  ));
                })()}
              </div>
              <a className="local-link" href="#social-teams">Explore all local markets <span>↗</span></a>
            </div>
            <div className="power-card">
              <span>POWER RANKINGS</span>
              <h3>{powerRankings?.week ? <>Week {powerRankings.week}<br />Top 3</> : <>Power<br />Rankings</>}</h3>
              <div className="power-teams">
                {(powerRankings?.rankings || []).slice(0, 3).map((team) => (
                  <span key={team.team_id}><b>{String(team.rank).padStart(2, "0")}</b><img src={logo(team.abbr)} alt="" />{team.abbr}<em>{team.record || ""}</em></span>
                ))}
                {!powerRankings?.rankings?.length && <span>Published with this week's column</span>}
              </div>
              <a href="#rankings">Full rankings →</a>
            </div>
          </aside>
        )}
      </div>
    </main>
  );
}
