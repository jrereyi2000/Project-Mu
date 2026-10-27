import { useEffect, useState } from "react";
import { API_BASE } from "../../api";

export function AuthorPage({ authorKey }: { authorKey: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/api/author/${encodeURIComponent(authorKey)}`)
      .then(res => res.json())
      .then(json => {
        if (!json.error) setData(json);
        setLoading(false);
      })
      .catch(err => {
        console.error("Error loading author:", err);
        setLoading(false);
      });
  }, [authorKey]);

  if (loading) {
    return (
      <main className="author-page">
        <div className="author-loading">Loading author archive...</div>
      </main>
    );
  }

  if (!data || !data.author) {
    return (
      <main className="author-page">
        <div className="author-not-found">
          <h2>Author not found</h2>
          <p>No profile found for "{authorKey}".</p>
          <a href="#" className="back-link">← Return to Newsroom</a>
        </div>
      </main>
    );
  }

  const { author, articles, total_articles } = data;
  const initials = author.display_name.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "GW";
  const teamColor = author.team?.team_color || "var(--red)";

  return (
    <main className="author-page" style={{ "--author-team-color": teamColor } as any}>
      <header className="author-profile-header">
        <div className="author-profile-main">
          <div className="author-avatar-wrap">
            <span className="avatar author-large-avatar" style={{ background: teamColor }}>{initials}</span>
          </div>
          <div className="author-meta-info">
            <div className="author-role-pills">
              <span className="author-badge">{author.archetype?.replace(/_/g, " ")}</span>
              {author.team && (
                <a href={`#team-${author.team.abbreviation.toLowerCase()}`} className="author-team-badge">
                  {author.team.name}
                </a>
              )}
            </div>
            <h1>{author.display_name}</h1>
            <div className="author-handle-row">
              <span className="author-handle">{author.handle}</span>
              <span className="author-archive-stat"><strong>{total_articles}</strong> Articles in Archive</span>
            </div>
            <p className="author-bio">{author.bio}</p>
          </div>
        </div>
      </header>

      <section className="author-articles-section">
        <div className="author-section-title">
          <span>Writing Archive</span>
          <h2>Published Work ({total_articles})</h2>
        </div>

        {articles && articles.length > 0 ? (
          <div className="author-articles-grid">
            {articles.map((art: any, index: number) => (
              <article className="author-article-card" key={art.id}>
                <div className="author-card-header">
                  <span className="story-number">{index + 1 < 10 ? `0${index + 1}` : index + 1}</span>
                  <span className="kicker">WEEK {art.week} · {art.label}</span>
                </div>
                <div className="author-card-body">
                  <a href={`#article-${art.id}`}>
                    <h3>{art.headline}</h3>
                  </a>
                  <p>{art.excerpt}</p>
                </div>
                <div className="author-card-body">
                  {art.tagged_teams && art.tagged_teams.length > 0 && (
                    <div className="author-tagged-chips">
                      {art.tagged_teams.slice(0, 3).map((t: any) => (
                        <a href={`#team-${t.abbr.toLowerCase()}`} key={t.id} className="team-chip-tag">
                          {t.abbr}
                        </a>
                      ))}
                    </div>
                  )}
                  <small>Read Article →</small>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="author-empty-archive">
            <p>No articles published yet.</p>
          </div>
        )}
      </section>
    </main>
  );
}
