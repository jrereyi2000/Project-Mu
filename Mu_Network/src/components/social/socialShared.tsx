import { useEffect, useState, type ReactNode } from "react";
import { API_BASE } from "../../api";
import { logo } from "../../lib/gridiron";

export type SocialIconName = "home" | "search" | "bell" | "bookmark" | "desk" | "teams" | "reply" | "repost" | "like" | "chart" | "more";

export function SocialIcon({ name }: { name: SocialIconName }) {
  const paths: Record<SocialIconName, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    bookmark: <path d="M6 3h12v18l-6-4-6 4Z" />,
    desk: <><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h5" /></>,
    teams: <><circle cx="9" cy="9" r="3" /><circle cx="17" cy="8" r="2" /><path d="M3 20c0-4 2-7 6-7s6 3 6 7M15 13c3 0 5 2 5 5" /></>,
    reply: <path d="M20 17a8 8 0 0 0-13-6l-3 3m0 0 4 3m-4-3h10" />,
    repost: <><path d="m7 7 3-3 3 3M10 4v12" /><path d="m17 17-3 3-3-3M14 20V8" /></>,
    like: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z" />,
    chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></>,
    more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

// ---------------------------------------------------------------------------------------------------------
// Agent Wire (docs/done/SOCIAL_MATRIX.md; data from /api/social/*, served by server/social_api.py)
// ---------------------------------------------------------------------------------------------------------
export const SOCIAL_API = `${API_BASE}/api/social`;

export type SocialAuthor = {
  agent_id: string;
  handle: string;
  display_name: string;
  archetype: string;
  label: string;
  team_abbr: string | null;
  team_color: string | null;
  followers: number;
};

export type SocialPostData = {
  id: number;
  week: number;
  kind: string;
  kind_label: string;
  text: string;
  posted_at: string | null;
  likes: number;
  reposts: number;
  replies: number;
  views: number;
  hashtags: string[];
  team_abbr: string | null;
  local: boolean;
  author: SocialAuthor;
  reply_to: { id: number; handle: string; display_name: string } | null;
  article: { id: number; headline: string; excerpt: string | null; article_type: string | null } | null;
};

export type SocialFeedData = {
  season: number;
  week: number | null;
  weeks: number[];
  as_of: string | null;
  posts: SocialPostData[];
  total?: number;
  next_offset: number | null;
};

export type SocialTrend = { id: string; rank: number; kind: string; key: string; title: string; category: string; description: string; volume: number };

export function useSocialData<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(url));
  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json) => { if (live) setData(json); })
      .catch((err) => { if (live) setError(String(err?.message || err)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [url]);
  return { data, error, loading, setData };
}

export const formatSocialCount = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
};

export const SOCIAL_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Posts carry simulated in-world times (no time zone). The feed's "now" is its newest post (as_of).
export const socialTime = (iso: string | null | undefined) => (iso ? new Date(iso) : null);

export const socialRelativeTime = (iso: string | null, asOf?: string | null) => {
  const t = socialTime(iso);
  if (!t) return "";
  const now = socialTime(asOf);
  if (now) {
    const mins = (now.getTime() - t.getTime()) / 60000;
    if (mins >= 0 && mins < 60) return `${Math.max(1, Math.round(mins))}m`;
    if (mins >= 0 && mins < 24 * 60) return `${Math.round(mins / 60)}h`;
  }
  return `${SOCIAL_MONTHS[t.getMonth()]} ${t.getDate()}`;
};

export const socialFullTime = (iso: string | null) => {
  const t = socialTime(iso);
  if (!t) return "";
  const h = t.getHours();
  const clock = `${h % 12 || 12}:${String(t.getMinutes()).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  return `${clock} · ${SOCIAL_MONTHS[t.getMonth()]} ${t.getDate()}, ${t.getFullYear()}`;
};

export const socialInitials = (name: string) => {
  const words = (name || "").replace(/[^\p{L}\p{N}\s'-]/gu, "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
};

// Avatars are parked (docs/done/SOCIAL_MATRIX.md): initials on the team colour, or a colour per kind of account.
export const SOCIAL_ACCOUNT_COLORS: Record<string, string> = {
  SCHEFTER_TWEET: "#b3122e", RAPOPORT: "#b3122e", NFL_MEMES: "#6d3fd1", DRAFT_SICKO: "#0e7490",
  FANTASY_GURU: "#15803d", BERRY: "#15803d", YATES: "#15803d", BANDWAGON_FAN: "#b45309",
};

export const socialAvatarColor = (author: { archetype?: string; team_color?: string | null }) =>
  author.team_color || SOCIAL_ACCOUNT_COLORS[author.archetype || ""] || "#334155";

export function SocialAvatar({ author, size = 46 }: { author: { display_name: string; archetype?: string; team_color?: string | null }; size?: number }) {
  return (
    <span
      className="social-avatar"
      style={{ width: size, height: size, background: socialAvatarColor(author), fontSize: Math.round(size * 0.36) }}
      aria-hidden="true"
    >
      {socialInitials(author.display_name)}
    </span>
  );
}

export const socialBadgeClass = (archetype: string) =>
  archetype === "LOCAL_BEAT_REPORTER" ? "beat"
    : archetype === "DIE_HARD_FAN" ? "homer"
    : archetype === "BANDWAGON_FAN" ? "bandwagon"
    : archetype === "FORMER_PLAYER" ? "legend"
    : archetype === "NFL_MEMES" || archetype === "SKIP_TWEET" || archetype === "STEPHEN_A" ? "hot"
    : "analyst";

export const socialHandlePath = (handle: string) => (handle || "").replace(/^@/, "");

export const socialArticleHref = (article: { id: number; article_type: string | null }) =>
  (article.article_type || "").startsWith("FANTASY_") ? `#article-fantasy-${article.id}` : `#article-${article.id}`;

export const socialTrendHref = (kind: string, key: string) => `#social-trend-${kind}-${key}`;

export function SocialText({ text }: { text: string }) {
  const parts = (text || "").split(/(#\w+|@\w+)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (/^#\w+$/.test(part)) {
          return <a className="social-link" href={socialTrendHref("HASHTAG", part.slice(1).toLowerCase())} key={i}>{part}</a>;
        }
        if (/^@\w+$/.test(part)) {
          return <a className="social-link" href={`#social-profile-${part.slice(1)}`} key={i}>{part}</a>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

export function SocialShareCard({ article }: { article: NonNullable<SocialPostData["article"]> }) {
  return (
    <a className="social-share-card" href={socialArticleHref(article)}>
      <span>{(article.article_type || "").startsWith("FANTASY_") ? "MU FANTASY" : "GRIDIRON NEWSROOM"}</span>
      <strong>{article.headline}</strong>
      {article.excerpt && <small>{article.excerpt}</small>}
    </a>
  );
}

export function SocialPost({ post, asOf }: { post: SocialPostData; asOf?: string | null }) {
  const profileHref = `#social-profile-${socialHandlePath(post.author.handle)}`;
  const openPost = () => {
    window.location.hash = `#social-post-${post.id}`;
  };

  return (
    <article
      className="social-post social-post-link"
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("button, a")) openPost();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === event.currentTarget) openPost();
      }}
      role="link"
      tabIndex={0}
      aria-label={`Open post by ${post.author.display_name}`}
    >
      <a className="social-post-avatar-link" href={profileHref} aria-label={`View ${post.author.display_name}'s profile`}>
        <SocialAvatar author={post.author} />
      </a>
      <div className="social-post-body">
        <div className="social-post-meta">
          <a className="social-post-author" href={profileHref}>
            <strong>{post.author.display_name}</strong>
            <span>{post.author.handle}</span>
          </a>
          <small>· {socialRelativeTime(post.posted_at, asOf)}</small>
          {post.author.team_abbr && <img src={logo(post.author.team_abbr)} alt={post.author.team_abbr} />}
          <b className={socialBadgeClass(post.author.archetype)}>{post.author.label}</b>
        </div>
        {post.reply_to && (
          <div className="social-reply-to">
            Replying to <a href={`#social-profile-${socialHandlePath(post.reply_to.handle)}`}>{post.reply_to.handle}</a>
          </div>
        )}
        <p><SocialText text={post.text} /></p>
        {post.article && <SocialShareCard article={post.article} />}
        <div className="social-post-actions">
          <button aria-label={`${post.replies} replies`}><SocialIcon name="reply" /><span>{formatSocialCount(post.replies)}</span></button>
          <button aria-label={`${post.reposts} reposts`}><SocialIcon name="repost" /><span>{formatSocialCount(post.reposts)}</span></button>
          <button aria-label={`${post.likes} likes`}><SocialIcon name="like" /><span>{formatSocialCount(post.likes)}</span></button>
        </div>
      </div>
    </article>
  );
}

export function SocialStatus({ loading, error, empty, emptyTitle, emptyText }: {
  loading: boolean; error: string | null; empty: boolean; emptyTitle: string; emptyText: string;
}) {
  if (loading) return <div className="social-empty"><span>Loading Agent Wire…</span></div>;
  if (error) {
    return (
      <div className="social-empty">
        <strong>Agent Wire couldn't load</strong>
        <span>Check that the API is running on localhost:8000, then refresh. ({error})</span>
      </div>
    );
  }
  if (empty) return <div className="social-empty"><strong>{emptyTitle}</strong><span>{emptyText}</span></div>;
  return null;
}

/** A paged list of posts from one /api/social feed URL (without offset). */
export function SocialPostList({ url, emptyTitle, emptyText, onLoaded }: {
  url: string; emptyTitle: string; emptyText: string; onLoaded?: (data: SocialFeedData) => void;
}) {
  const { data, error, loading } = useSocialData<SocialFeedData>(url);
  const [extra, setExtra] = useState<SocialPostData[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setExtra([]);
    setNextOffset(data?.next_offset ?? null);
    if (data && onLoaded) onLoaded(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const loadMore = () => {
    if (nextOffset == null) return;
    setLoadingMore(true);
    fetch(`${url}${url.includes("?") ? "&" : "?"}offset=${nextOffset}`)
      .then((res) => res.json())
      .then((more: SocialFeedData) => {
        setExtra((prev) => [...prev, ...(more.posts || [])]);
        setNextOffset(more.next_offset ?? null);
      })
      .catch(() => setNextOffset(null))
      .finally(() => setLoadingMore(false));
  };

  const posts = [...(data?.posts || []), ...extra];
  return (
    <section className="social-timeline">
      <SocialStatus loading={loading} error={error} empty={!loading && !error && posts.length === 0}
        emptyTitle={emptyTitle} emptyText={emptyText} />
      {posts.map((post) => <SocialPost post={post} asOf={data?.as_of} key={post.id} />)}
      {nextOffset != null && (
        <button className="social-load-more" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Show more posts"}
        </button>
      )}
    </section>
  );
}
