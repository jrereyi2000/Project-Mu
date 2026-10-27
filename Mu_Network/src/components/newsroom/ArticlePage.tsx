import { useContext, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { SystemContext } from "../../context/SystemContext";
import { getPlayerProfile, localStories, stories, teams } from "../../lib/gridiron";
import { ArticleMarkdown } from "./ArticleMarkdown";

export function ArticlePage({ articleId }: { articleId: string }) {
  const systemStatus = useContext(SystemContext);
  const [dbArticle, setDbArticle] = useState<any>(null);
  const isDbArticle = /^\d+$/.test(articleId);

  useEffect(() => {
    if (isDbArticle) {
      fetch(`${API_BASE}/api/news/article/${articleId}`)
        .then(res => res.json())
        .then(data => {
          if (!data.error) setDbArticle(data);
        })
        .catch(err => console.error("Error loading article:", err));
    }
  }, [articleId, isDbArticle]);

  const nationalMatch = articleId.match(/^national-(\d+)$/);
  const localMatch = articleId.match(/^local-([a-z]+)$/);
  const teamHeroMatch = articleId.match(/^team-hero-([a-z]+)$/);
  const teamStoryMatch = articleId.match(/^team-([a-z]+)-(\d+)$/);
  const playerStoryMatch = articleId.match(/^player-(passing|rushing|receiving|scoring|tightend|fullback)-(\d+)-(\d+)$/);
  let data = {
    category: "The Big Read",
    title: "One night. Two contenders. Everything on the line.",
    dek: "The league's most explosive offense meets the defense built to stop it. Inside the tactical battle that will define the season.",
    author: "Bill Barnwell",
    role: "National Writer",
    initials: "BB",
    subject: "the league's defining matchup",
    image: "https://images.unsplash.com/photo-1634306320557-4c4e4660692f?auto=format&fit=crop&w=1800&q=88",
    body: null as string | null,
    teams: [] as any[],
    players: [] as any[],
    authorHandle: null as string | null | undefined,
    authorBio: null as string | null | undefined,
    related: [] as any[],
  };

  if (isDbArticle && dbArticle) {
    const authorName = dbArticle.author?.display_name || dbArticle.persona || "Staff Writer";
    const initials = authorName.split(" ").map((p: string) => p[0]).join("").slice(0, 2).toUpperCase() || "NW";
    const primaryTeam = dbArticle.tagged_teams?.find((t: any) => t.mention_type === 'PRIMARY') || dbArticle.tagged_teams?.[0];
    const catLabel = dbArticle.article_type ? dbArticle.article_type.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "Analysis";
    data = {
      ...data,
      category: catLabel,
      title: dbArticle.headline || data.title,
      dek: dbArticle.excerpt || data.dek,
      author: authorName,
      authorHandle: dbArticle.author?.handle,
      authorBio: dbArticle.author?.bio,
      role: dbArticle.author?.archetype ? `${dbArticle.author.archetype.replace(/_/g, " ")}` : "Staff Writer",
      initials,
      subject: primaryTeam ? primaryTeam.name : "the league",
      body: dbArticle.content || "",
      teams: dbArticle.tagged_teams || [],
      players: dbArticle.tagged_players || [],
      related: dbArticle.related_articles || [],
    };
  } else if (nationalMatch) {
    const story = stories[Number(nationalMatch[1])] ?? stories[0];
    data = { ...data, category: story.kicker, title: story.title, dek: story.blurb, author: story.author, authorHandle: null, authorBio: null, role: "National Reporter", initials: story.initials, subject: story.title.toLowerCase(), related: [] };
  } else if (localMatch) {
    const story = localStories.find((item) => item.team === localMatch[1]) ?? localStories[0];
    const team = teams.find((item) => item.abbr === story.team) ?? teams[0];
    data = { ...data, category: story.tag, title: story.title, dek: `Inside the decisions, matchups and personalities shaping the latest chapter for ${team.name}.`, author: story.author, authorHandle: null, authorBio: null, role: `${team.name} Beat Reporter`, initials: story.author.split(" ").map((part) => part[0]).join(""), subject: team.name, related: [] };
  } else if (teamHeroMatch || teamStoryMatch) {
    const abbr = teamHeroMatch?.[1] ?? teamStoryMatch?.[1] ?? "sf";
    const team = teams.find((item) => item.abbr === abbr) ?? teams[0];
    const storyIndex = Number(teamStoryMatch?.[2] ?? 0);
    const titles = [
      `Inside the belief driving ${team.name} into the stretch run`,
      `The adjustment that could unlock the ${team.name} postseason run`,
      `Three keys for ${team.name} heading into Week 17`,
      `Why the ${team.name} young core is ahead of schedule`,
    ];
    data = {
      ...data,
      category: teamHeroMatch ? "Exclusive" : "Team Coverage",
      title: titles[storyIndex % titles.length],
      dek: `One locker room, one shared standard and a season still waiting to be defined. A closer look inside ${team.name}.`,
      author: "Jordan Ellis",
      authorHandle: null,
      authorBio: null,
      role: `${team.name} Beat Reporter`,
      initials: "JE",
      subject: team.name,
      image: "https://images.unsplash.com/photo-1704713611761-e0fbbdecc3e9?auto=format&fit=crop&w=1800&q=85",
      related: [],
    };
  } else if (playerStoryMatch) {
    const category = playerStoryMatch[1];
    const playerId = Number(playerStoryMatch[2]);
    const storyIndex = Number(playerStoryMatch[3]);
    const player = getPlayerProfile(category, playerId);
    const titles = [
      `How ${player.name} is changing the geometry of the ${player.team.name} offense`,
      `${player.name}'s preparation is becoming the standard inside the building`,
      `The matchup that could decide ${player.name}'s Week 17 impact`,
      `What the advanced metrics reveal about ${player.name}'s breakout`,
      `${player.name} on leadership, pressure and the postseason chase`,
      `Where ${player.name} ranks among the league's best at ${player.position}`,
    ];
    data = { ...data, category: "Player Coverage", title: titles[storyIndex % titles.length], dek: `The film, numbers and voices behind ${player.name}'s impact on the ${player.team.name} this season.`, author: "Jordan Ellis", authorHandle: null, authorBio: null, role: "National Reporter", initials: "JE", subject: player.name, related: [] };
  }

  return (
    <main className="article-page">
      <article>
        <header className="article-header">
          <div className="article-breadcrumb"><a href="#">The Newsroom</a><span>/</span><b>{data.category}</b></div>
          <span className="article-kicker">{data.category}</span>
          <h1>{data.title}</h1>
          <p>{data.dek}</p>
          <div className="article-byline">
            <span className="avatar red">{data.initials}</span>
            <div>
              <strong>
                {data.authorHandle ? (
                  <a href={`#author-${data.authorHandle.replace(/^@/, '')}`} className="author-byline-link">{data.author}</a>
                ) : data.author}
              </strong>
              <span>{data.role}</span>
            </div>
            <div><small>PUBLISHED</small><b>{dbArticle?.week ? (dbArticle.week === 24 ? `Offseason Edition, ${systemStatus.season} Season` : `Week ${dbArticle.week}, ${systemStatus.season} Season`) : `December 23, ${systemStatus.season} · 9:15 AM ET`}</b></div>
            <div><small>READ TIME</small><b>9 min</b></div>
          </div>
        </header>

        <figure className="article-hero-media">
          <img src={data.image} alt="Football action under stadium lights" />
          <figcaption>Week 17 brings the season's biggest questions into sharper focus. <span>GRIDIRON IMAGES</span></figcaption>
        </figure>

        <div className="article-layout">
          <aside className="article-share">
            <span>SHARE</span>
            <button aria-label="Copy article link">↗</button>
            <button aria-label="Share by email">@</button>
          </aside>
          <div className="article-body">
            {data.body ? (
              <ArticleMarkdown content={data.body} />
            ) : (
              <>
                <p className="article-lead">There is a point late in every season when possibility becomes pressure. For {data.subject}, that moment has arrived—not as a single play or result, but as a collection of details that have started to define everything.</p>
                <p>The evidence appears on film before it reaches the box score. Alignments are cleaner. Decisions arrive a beat faster. The space between a promising idea and a winning one has narrowed, giving coaches confidence to reach deeper into the call sheet when the game demands it.</p>
                <h2>The details behind the rise</h2>
                <p>On early downs, the approach has become more varied without becoming more complicated. Motion reveals coverage. Formations create leverage. The result is an offense playing with answers instead of searching for them after the snap.</p>
                <blockquote><span>“</span>The best teams don't wait for the game to tell them who they are. They impose that identity from the opening series.</blockquote>
                <p>That confidence extends beyond the headline numbers. Teammates point to preparation, communication and a willingness to live with the correct decision even when the outcome is imperfect. Over the course of a long season, those habits compound.</p>
                <h2>What comes next</h2>
                <p>The remaining challenge is translating control into consistency. Opponents will change the picture, test protection rules and force the ball toward uncomfortable matchups. The response will determine whether this run becomes a memorable stretch or something larger.</p>
                <p>For now, the trajectory is clear. The stakes are rising, the margin for error is shrinking and {data.subject} is entering the decisive part of the calendar with a real opportunity to shape what happens next.</p>
              </>
            )}
            <div className="article-author-note">
              <span className="avatar">{data.initials}</span>
              <div>
                <strong>
                  {data.authorHandle ? (
                    <a href={`#author-${data.authorHandle.replace(/^@/, '')}`} className="author-byline-link">{data.author}</a>
                  ) : data.author}
                </strong>
                <p>{data.authorBio || `${data.author} covers the league for Gridiron, specializing in film analysis, roster construction and the stories behind the numbers.`}</p>
              </div>
            </div>
          </div>
          <aside className="article-related">
            <span>KEEP READING</span>
            {((data as any).related && (data as any).related.length > 0 ? (data as any).related.slice(0, 4) : stories.slice(0, 3)).map((story: any, index: number) => {
              const link = story.id ? `#article-${story.id}` : `#article-national-${index}`;
              const title = story.headline || story.title;
              return (
                <a href={link} key={story.id || story.title}>
                  <small>0{index + 1}</small>
                  <strong>{title}</strong>
                  <i>→</i>
                </a>
              );
            })}
          </aside>
        </div>
      </article>
    </main>
  );
}
