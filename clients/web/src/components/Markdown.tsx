import { type ReactNode } from "react";

/**
 * Tiny, dependency-free Markdown renderer for assistant chat replies.
 *
 * It builds React nodes directly (no `dangerouslySetInnerHTML`, so no XSS
 * surface) and covers the constructs LLMs actually emit: headings, bold,
 * italics, inline code, fenced code blocks, ordered/unordered lists,
 * blockquotes, links, horizontal rules and paragraphs. It is deliberately
 * not full CommonMark — nested lists and tables fall back to flat text.
 *
 * Unclosed inline markers (mid-stream `**`, an open code fence) render as
 * literal text until their closer arrives, so streaming looks sane.
 */
export function Markdown({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="space-y-2 [overflow-wrap:anywhere]">
      {renderBlocks(text)}
    </div>
  );
}

const INLINE_CODE = "rounded bg-foreground/10 px-1 py-0.5 font-mono text-[12px]";

function headingClass(level: number): string {
  if (level <= 1) return "text-[15px] font-semibold text-foreground";
  if (level === 2) return "text-[14px] font-semibold text-foreground";
  return "text-[13.5px] font-semibold text-foreground";
}

function isBlockStart(line: string): boolean {
  return (
    /^```/.test(line) ||
    /^(#{1,6})\s+/.test(line) ||
    /^\s*([-*_])\1\1+\s*$/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*[-*+]\s+/.test(line) ||
    /^\s*\d+\.\s+/.test(line)
  );
}

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block.
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      i++; // consume the closing fence if present
      out.push(
        <pre
          key={key++}
          className="overflow-x-auto rounded-lg bg-foreground/10 p-2.5 font-mono text-[12px] leading-relaxed"
        >
          <code className="whitespace-pre">{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // Blank line.
    if (/^\s*$/.test(line)) {
      i++;
      continue;
    }

    // Horizontal rule (---, ***, ___).
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      out.push(<hr key={key++} className="border-border" />);
      i++;
      continue;
    }

    // Heading.
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      out.push(
        <p key={key++} className={headingClass(h[1].length)}>
          {renderInline(h[2], `h${key}`)}
        </p>,
      );
      i++;
      continue;
    }

    // Blockquote (consume the run, strip markers, recurse).
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      out.push(
        <blockquote
          key={key++}
          className="space-y-2 border-l-2 border-border pl-3 text-muted-foreground"
        >
          {renderBlocks(buf.join("\n"))}
        </blockquote>,
      );
      continue;
    }

    // Unordered list.
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      const k = key++;
      out.push(
        <ul key={k} className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ul${k}-${j}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Ordered list.
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      const k = key++;
      out.push(
        <ol key={k} className="list-decimal space-y-1 pl-5 marker:text-muted-foreground">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ol${k}-${j}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // Paragraph: gather until a blank line or the next block.
    const buf: string[] = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !isBlockStart(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    const k = key++;
    out.push(
      <p key={k} className="leading-relaxed">
        {buf.flatMap((ln, j) =>
          j === 0
            ? renderInline(ln, `p${k}-${j}`)
            : [<br key={`br${k}-${j}`} />, ...renderInline(ln, `p${k}-${j}`)],
        )}
      </p>,
    );
  }

  return out;
}

// Inline patterns, in tie-break priority order (earliest match wins; ties
// resolve by this order so `**x**` is bold, not nested italic).
const PRIORITY = ["code", "bold", "link", "italic"] as const;
type InlineType = (typeof PRIORITY)[number];
const PATTERNS: { type: InlineType; re: RegExp }[] = [
  { type: "code", re: /`([^`]+)`/ },
  { type: "bold", re: /\*\*([\s\S]+?)\*\*/ },
  { type: "link", re: /\[([^\]]+)\]\(([^)\s]+)\)/ },
  { type: "italic", re: /\*([^*\n]+?)\*/ },
];

function renderInline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let n = 0;

  while (rest.length > 0) {
    let best: { type: InlineType; index: number; m: RegExpMatchArray } | null =
      null;
    for (const p of PATTERNS) {
      const m = rest.match(p.re);
      if (m && m.index !== undefined) {
        const better =
          best === null ||
          m.index < best.index ||
          (m.index === best.index &&
            PRIORITY.indexOf(p.type) < PRIORITY.indexOf(best.type));
        if (better) best = { type: p.type, index: m.index, m };
      }
    }

    if (!best) {
      out.push(rest);
      break;
    }
    if (best.index > 0) out.push(rest.slice(0, best.index));

    const key = `${keyBase}-${n++}`;
    if (best.type === "code") {
      out.push(
        <code key={key} className={INLINE_CODE}>
          {best.m[1]}
        </code>,
      );
    } else if (best.type === "bold") {
      out.push(
        <strong key={key} className="font-semibold">
          {renderInline(best.m[1], key)}
        </strong>,
      );
    } else if (best.type === "italic") {
      out.push(<em key={key}>{renderInline(best.m[1], key)}</em>);
    } else {
      out.push(
        <a
          key={key}
          href={best.m[2]}
          target="_blank"
          rel="noreferrer"
          className="text-brand-600 underline underline-offset-2 hover:text-brand-700 dark:text-brand-400"
        >
          {renderInline(best.m[1], key)}
        </a>,
      );
    }
    rest = rest.slice(best.index + best.m[0].length);
  }

  return out;
}
