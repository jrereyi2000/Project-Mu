import type { ReactNode } from "react";

// Inline markdown: **bold**, *italic*, `code`, [link](url). Built as React nodes.
export function articleInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      out.push(<strong key={m.index}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      out.push(<em key={m.index}>{m[2]}</em>);
    } else if (m[3] !== undefined) {
      out.push(<code key={m.index}>{m[3]}</code>);
    } else if (m[4] !== undefined) {
      out.push(
        <a key={m.index} href={m[5]} target={m[5].startsWith("#") ? undefined : "_blank"} rel="noreferrer">
          {m[4]}
        </a>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Markdown renderer for newsroom articles: headings, lists, blockquotes, tables, dividers, and paragraphs.
export function ArticleMarkdown({ content }: { content: string }) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let firstParagraph = true;

  const flush = () => {
    if (!para.length) return;
    const text = para.join(" ").trim();
    para = [];
    if (!text) return;
    blocks.push(
      <p key={blocks.length} className={firstParagraph ? "article-lead" : undefined}>
        {articleInline(text)}
      </p>
    );
    firstParagraph = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }
    // Skip H1 title and excerpt as they are already displayed in the article header
    if (/^#\s/.test(line) || /^>\s*\*\*EXCERPT:\*\*/i.test(line) || /^\*\*EXCERPT:\*\*/i.test(line)) {
      flush();
      continue;
    }
    // Horizontal rule: --- or ***
    if (/^(-{3,}|\*{3,})$/.test(line)) {
      flush();
      blocks.push(<hr key={blocks.length} />);
      continue;
    }
    // Headings ##, ###, ####
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      if (level === 2) {
        blocks.push(<h2 key={blocks.length}>{articleInline(heading[2])}</h2>);
      } else if (level === 3) {
        blocks.push(<h3 key={blocks.length}>{articleInline(heading[2])}</h3>);
      } else {
        blocks.push(<h4 key={blocks.length}>{articleInline(heading[2])}</h4>);
      }
      continue;
    }
    // Blockquote: > quote
    if (line.startsWith(">")) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      i--;
      blocks.push(
        <blockquote key={blocks.length}>
          <span>“</span>
          {articleInline(quote.join(" "))}
        </blockquote>
      );
      continue;
    }
    // Table: | col | col |
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
        <div className="article-table-wrap" key={blocks.length}>
          <table>
            {head && (
              <thead>
                <tr>
                  {head.map((c, k) => (
                    <th key={k}>{articleInline(c)}</th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {body.map((r, k) => (
                <tr key={k}>
                  {r.map((c, j) => (
                    <td key={j}>{articleInline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }
    // Lists: bullet or numbered
    const isBullet = /^[-*•]\s+/.test(line);
    const isNumber = /^\d+[.)]\s+/.test(line);
    if (isBullet || isNumber) {
      flush();
      const items: string[] = [];
      const itemRe = isBullet ? /^[-*•]\s+/ : /^\d+[.)]\s+/;
      while (i < lines.length) {
        const curLine = lines[i].trim();
        if (itemRe.test(curLine)) {
          items.push(curLine.replace(itemRe, ""));
        } else if (curLine && (lines[i].startsWith("  ") || lines[i].startsWith("\t"))) {
          if (items.length) {
            items[items.length - 1] += " " + curLine;
          } else {
            break;
          }
        } else {
          break;
        }
        i++;
      }
      i--;
      const children = items.map((it, k) => <li key={k}>{articleInline(it)}</li>);
      blocks.push(isNumber ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
      continue;
    }

    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}
