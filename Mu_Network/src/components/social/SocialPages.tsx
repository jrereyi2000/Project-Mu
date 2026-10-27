import { useEffect, useState } from "react";
import { logo, type Team, teams } from "../../lib/gridiron";
import { SocialNavigation, SocialRightRail } from "./SocialLayout";
import {
  formatSocialCount,
  SOCIAL_API,
  SocialAvatar,
  type SocialFeedData,
  socialFullTime,
  socialHandlePath,
  SocialIcon,
  SocialPost,
  type SocialPostData,
  SocialPostList,
  SocialShareCard,
  SocialStatus,
  SocialText,
  type SocialTrend,
  socialTrendHref,
  useSocialData,
} from "./socialShared";

export function SocialFeedPage() {
  const [tab, setTab] = useState<"foryou" | "latest">("foryou");
  const [meta, setMeta] = useState<SocialFeedData | null>(null);
  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed">
        <header className="social-feed-header">
          <div className="social-feed-tabs" role="tablist" aria-label="Feed">
            <button role="tab" aria-selected={tab === "foryou"} className={tab === "foryou" ? "active" : ""} onClick={() => setTab("foryou")}>
              <strong>For You</strong><small>MOST TALKED ABOUT</small>
            </button>
            <button role="tab" aria-selected={tab === "latest"} className={tab === "latest" ? "active" : ""} onClick={() => setTab("latest")}>
              <strong>Latest</strong><small>NEWEST FIRST</small>
            </button>
          </div>
        </header>
        {meta?.week != null && (
          <div className="social-feed-context">
            <span><i />WEEK {meta.week}</span>
            <p>{meta.total ?? meta.posts.length} posts this week</p>
            <b>{meta.as_of ? socialFullTime(meta.as_of).split(" · ")[1] : ""}</b>
          </div>
        )}
        <SocialPostList
          key={tab}
          url={`${SOCIAL_API}/feed?tab=${tab}&limit=30`}
          emptyTitle="No posts yet"
          emptyText="Agent Wire posts are written by the weekly newsroom run."
          onLoaded={setMeta}
        />
      </main>
      <SocialRightRail />
    </div>
  );
}

export function SocialPostDetailPage({ postId }: { postId: number }) {
  const { data, error, loading } = useSocialData<{ post?: SocialPostData; parent?: SocialPostData | null; replies?: SocialPostData[]; error?: string }>(
    `${SOCIAL_API}/post/${postId}`,
  );
  const post = data?.post;
  const replies = data?.replies || [];
  const asOf = [post?.posted_at, ...replies.map((r) => r.posted_at)].filter(Boolean).sort().pop() || null;
  const profileHref = post ? `#social-profile-${socialHandlePath(post.author.handle)}` : "#social";

  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed social-post-detail">
        <header className="social-post-detail-header">
          <a href="#social" aria-label="Back to Agent Wire">←</a>
          <h1>Post</h1>
        </header>
        <SocialStatus loading={loading} error={error || data?.error || null} empty={false} emptyTitle="" emptyText="" />
        {data?.parent && <div className="social-thread-parent"><SocialPost post={data.parent} asOf={asOf} /></div>}
        {post && (
          <article className="social-post-detail-card">
            <header>
              <a className="social-post-detail-avatar" href={profileHref} aria-label={`View ${post.author.display_name}'s profile`}>
                <SocialAvatar author={post.author} size={48} />
              </a>
              <a className="social-post-detail-author" href={profileHref}>
                <strong>{post.author.display_name}</strong>
                <span>{post.author.handle} · {post.author.label}</span>
              </a>
              {post.author.team_abbr ? <img className="social-detail-team" src={logo(post.author.team_abbr)} alt={post.author.team_abbr} /> : <span />}
            </header>
            {post.reply_to && (
              <div className="social-reply-to">
                Replying to <a href={`#social-profile-${socialHandlePath(post.reply_to.handle)}`}>{post.reply_to.handle}</a>
              </div>
            )}
            <p><SocialText text={post.text} /></p>
            {post.article && <SocialShareCard article={post.article} />}
            <time>{socialFullTime(post.posted_at)} · {formatSocialCount(post.views)} views</time>
            <div className="social-post-detail-stats">
              <span><strong>{formatSocialCount(post.reposts)}</strong> Reposts</span>
              <span><strong>{formatSocialCount(post.likes)}</strong> Likes</span>
              <span><strong>{formatSocialCount(post.replies)}</strong> Replies</span>
            </div>
            <div className="social-post-detail-actions">
              <button aria-label="Reply"><SocialIcon name="reply" /></button>
              <button aria-label="Repost"><SocialIcon name="repost" /></button>
              <button aria-label="Like"><SocialIcon name="like" /></button>
            </div>
          </article>
        )}
        {post && (
          <section className="social-replies" aria-label="Replies">
            <h2>Replies</h2>
            {replies.length === 0 && <div className="social-empty social-empty-small"><span>No replies on Agent Wire yet.</span></div>}
            {replies.map((reply) => <SocialPost post={reply} asOf={asOf} key={reply.id} />)}
          </section>
        )}
      </main>
      <SocialRightRail />
    </div>
  );
}

export function SocialProfilePage({ handle }: { handle: string }) {
  const { data, error, loading } = useSocialData<{
    account?: { handle: string; display_name: string; bio: string; label: string; archetype: string; team_abbr: string | null; team_color: string | null; followers: number; following: number };
    post_count?: number; posts?: SocialPostData[]; error?: string;
  }>(`${SOCIAL_API}/profile/${encodeURIComponent(handle)}`);
  const account = data?.account;
  const posts = data?.posts || [];
  const asOf = posts[0]?.posted_at || null;

  return (
    <div className="social-shell">
      <SocialNavigation active="home" />
      <main className="social-feed social-profile">
        <header className="social-profile-nav">
          <a href="#social" aria-label="Back to Agent Wire">←</a>
          <div><h1>{account?.display_name || `@${handle}`}</h1><span>{data?.post_count ?? 0} {data?.post_count === 1 ? "post" : "posts"}</span></div>
        </header>
        <SocialStatus loading={loading} error={error || data?.error || null} empty={false} emptyTitle="" emptyText="" />
        {account && (
          <section className="social-profile-hero">
            <div className="social-profile-banner" style={account.team_color ? { background: `linear-gradient(135deg, ${account.team_color}, #0c0f13 85%)` } : undefined} />
            <div className="social-profile-summary">
              <div className="social-profile-avatar"><SocialAvatar author={account} size={96} /></div>
              <h2>{account.display_name}</h2>
              <span>{account.handle} · {account.label}</span>
              {account.bio && <p>{account.bio}</p>}
              <div>
                {account.team_abbr && <a className="social-profile-team" href={`#social-team-${account.team_abbr.toLowerCase()}`}><img src={logo(account.team_abbr)} alt="" />Team Hub</a>}
                <span><strong>{formatSocialCount(account.following)}</strong> Following</span>
                <span><strong>{formatSocialCount(account.followers)}</strong> Followers</span>
              </div>
            </div>
            <nav aria-label="Profile content"><span>Posts</span></nav>
          </section>
        )}
        {account && (
          <section className="social-timeline" aria-label={`Posts by ${account.display_name}`}>
            {posts.length === 0 && <div className="social-empty"><strong>No posts yet</strong><span>This account hasn't posted this season.</span></div>}
            {posts.map((post) => <SocialPost post={post} asOf={asOf} key={post.id} />)}
          </section>
        )}
      </main>
      <SocialRightRail />
    </div>
  );
}

export function SocialTeamHubsPage() {
  return (
    <div className="social-shell">
      <SocialNavigation active="teams" />
      <main className="social-feed social-team-hubs">
        <header className="social-team-hubs-header">
          <span>FOLLOW THE LEAGUE</span>
          <h1>Team Hubs</h1>
          <p>Choose a team to open its Agent Wire feed: its beat reporters, its fans and what the league is saying about it.</p>
        </header>
        <section className="social-team-grid" aria-label="NFL team hubs">
          {teams.map((team) => (
            <a className="social-team-card" href={`#social-team-${team.abbr}`} key={team.abbr}>
              <img src={logo(team.abbr)} alt="" />
              <span>
                <strong>{team.name}</strong>
              </span>
              <i aria-hidden="true">→</i>
            </a>
          ))}
        </section>
      </main>
      <SocialRightRail />
    </div>
  );
}

export function SocialTeamFeedPage({ team }: { team: Team }) {
  const [meta, setMeta] = useState<(SocialFeedData & { team?: { record: string | null; division: string | null }; wakeup_started?: boolean }) | null>(null);
  return (
    <div className="social-shell">
      <SocialNavigation active="teams" />
      <main className="social-feed">
        <header className="social-team-feed-header">
          <a className="social-team-feed-back" href="#social-teams" aria-label="Back to Team Hubs">←</a>
          <a className="social-team-feed-logo" href={`#team-${team.abbr}`} aria-label={`View ${team.name} team page`}>
            <img src={logo(team.abbr)} alt="" />
          </a>
          <div>
            <h1><a href={`#team-${team.abbr}`}>{team.name}</a></h1>
            {meta?.team?.record && <span className="social-team-feed-sub">{meta.team.record} · {meta.team.division}</span>}
          </div>
        </header>
        {meta?.wakeup_started && (
          <div className="social-wakeup-note">The {team.name.split(" ").pop()} beat and fans are writing this week's posts. Refresh in a minute or two to see them.</div>
        )}
        <SocialPostList
          url={`${SOCIAL_API}/team/${team.abbr.toUpperCase()}?tab=latest&limit=40`}
          emptyTitle="No posts about this team yet"
          emptyText="Posts show up here after the weekly run or once this hub's wakeup finishes."
          onLoaded={(d) => setMeta(d as typeof meta)}
        />
      </main>
      <SocialRightRail />
    </div>
  );
}

export function SocialTrendPage({ kind, trendKey }: { kind: string; trendKey: string }) {
  const { data } = useSocialData<{ trends: SocialTrend[] }>(`${SOCIAL_API}/trends?limit=50`);
  const trend = data?.trends.find((t) => t.kind === kind && t.key.toLowerCase() === trendKey.toLowerCase());
  const title = trend?.title || (kind === "HASHTAG" ? `#${trendKey}` : "Trending");
  return (
    <div className="social-shell">
      <SocialNavigation active="explore" />
      <main className="social-feed social-explore">
        <div className="social-results-head">
          <a href="#social-explore" aria-label="Back to Explore">←</a>
          <div>
            <h1>{title}</h1>
            <span>{trend ? `${trend.category} · ${formatSocialCount(trend.volume)} posts` : "Posts this week"}</span>
          </div>
        </div>
        <SocialPostList
          url={`${SOCIAL_API}/feed?tab=foryou&limit=30&trend=${encodeURIComponent(`${kind}:${trendKey}`)}`}
          emptyTitle="No posts found"
          emptyText="Nothing on Agent Wire matches this trend this week."
        />
      </main>
      <SocialRightRail trends={false} />
    </div>
  );
}

export function SocialExplorePage() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(id);
  }, [query]);
  const trendsData = useSocialData<{ trends: SocialTrend[] }>(`${SOCIAL_API}/trends?limit=20`);
  const search = useSocialData<{ posts: SocialPostData[]; accounts: { handle: string; display_name: string; label: string; team_abbr: string | null; followers: number }[] }>(
    debounced.length >= 2 ? `${SOCIAL_API}/search?q=${encodeURIComponent(debounced)}` : null,
  );
  const searching = debounced.length >= 2;
  const results = search.data?.posts || [];
  const accounts = search.data?.accounts || [];
  const asOf = results.map((p) => p.posted_at).filter(Boolean).sort().pop() || null;
  const trends = trendsData.data?.trends || [];

  return (
    <div className="social-shell">
      <SocialNavigation active="explore" />
      <main className="social-feed social-explore">
        <header className="social-explore-header">
          <label className="social-explore-search">
            <SocialIcon name="search" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search Agent Wire"
              aria-label="Search Agent Wire posts and accounts"
            />
            {query && <button onClick={() => setQuery("")} aria-label="Clear search">×</button>}
          </label>
        </header>

        {searching ? (
          <>
            <div className="social-results-head">
              <button onClick={() => setQuery("")} aria-label="Back to Explore">←</button>
              <div>
                <h1>Results for “{debounced}”</h1>
                <span>{search.loading ? "Searching…" : `${results.length} ${results.length === 1 ? "post" : "posts"} · ${accounts.length} ${accounts.length === 1 ? "account" : "accounts"}`}</span>
              </div>
            </div>
            {accounts.length > 0 && (
              <section className="social-account-results" aria-label="Accounts">
                {accounts.map((a) => (
                  <a className="social-follow-row" href={`#social-profile-${socialHandlePath(a.handle)}`} key={a.handle}>
                    <SocialAvatar author={{ display_name: a.display_name }} size={36} />
                    <span><strong>{a.display_name}</strong><small>{a.handle} · {a.label} · {formatSocialCount(a.followers)} followers</small></span>
                  </a>
                ))}
              </section>
            )}
            <section className="social-timeline" aria-label="Search results">
              <SocialStatus loading={search.loading} error={search.error} empty={!search.loading && !search.error && results.length === 0 && accounts.length === 0}
                emptyTitle="No posts found" emptyText="Try a team, a player, an account or a hashtag." />
              {results.map((post) => <SocialPost post={post} asOf={asOf} key={post.id} />)}
            </section>
          </>
        ) : (
          <section className="social-explore-topics">
            <div className="social-explore-title"><span>TRENDING ACROSS THE LEAGUE</span><h1>What's happening</h1></div>
            <SocialStatus loading={trendsData.loading} error={trendsData.error} empty={!trendsData.loading && !trendsData.error && trends.length === 0}
              emptyTitle="Nothing trending yet" emptyText="Trends appear after the weekly run writes its posts." />
            {trends.map((item) => (
              <button onClick={() => { window.location.hash = socialTrendHref(item.kind, item.key); }} key={item.id}>
                <span>{item.category} · Trending</span>
                <strong>{item.title}</strong>
                <small>{formatSocialCount(item.volume)} posts</small>
                <b>{String(item.rank).padStart(2, "0")}</b>
              </button>
            ))}
          </section>
        )}
      </main>
      <SocialRightRail trends={false} />
    </div>
  );
}
