import { Fragment, type ReactNode } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

// A deliberately small Markdown renderer. ui3 has no markdown dependency and we
// don't want one — agent output (final reports, checkpoint summaries) is
// GitHub-flavoured markdown, and rendering it as raw `whiteSpace: pre-wrap` text
// is the single biggest "log is too abstract" complaint. This covers the subset
// the agents actually emit: headings, bold/italic, inline + fenced code, links,
// ordered/unordered lists, blockquotes, tables, and horizontal rules. It is not
// a spec-complete parser; when in doubt it degrades to plain text rather than
// mangling. Theme-aware via the CSS vars in index.css.
//
// LaTeX math IS a dependency (KaTeX) — the one exception, because agents emit
// `$…$` / `$$…$$` freely (R13 makes LaTeX the default output format) and hand-
// rolling `\hat`, sub/superscripts, `\exp`, fractions is not viable. KaTeX is
// rendered with throwOnError:false so malformed math degrades to its source
// text (in the KaTeX error colour) rather than breaking the bubble.

// Render a TeX string to a KaTeX node. Never throws — bad input renders as the
// raw source, so a stray `$` can't blank out an agent's whole report.
function mathNode(tex: string, display: boolean, key: string): ReactNode {
  let html: string;
  try {
    html = katex.renderToString(tex, { displayMode: display, throwOnError: false });
  } catch {
    return (
      <code key={key} style={{ fontFamily: "var(--mono)", fontSize: "0.88em" }}>
        {tex}
      </code>
    );
  }
  return display ? (
    <div key={key} style={{ margin: "8px 0", overflowX: "auto" }} dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <span key={key} dangerouslySetInnerHTML={{ __html: html }} />
  );
}

// ---- inline ----

// Splits a run of text on inline spans (code, bold, italic, links) and returns
// React nodes. Order matters: code first so `**` inside backticks stays literal.
function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  // One regex, alternation ordered by precedence. Math sits right after code so
  // `$…$` content is protected from the bold/italic rules (e.g. the underscores
  // in `$\log A_0$` must not be eaten as italic). Groups:
  //  1 code  2 math  3 bold  4 italic  5 link-text  6 link-href
  // Inline math requires a non-space just inside each `$` so prose like
  // "it cost $5" is not mistaken for math.
  const re =
    /(`[^`]+`)|(\$(?!\s)[^$\n]*?(?<!\s)\$)|(\*\*[^*]+\*\*)|(\*[^*]+\*|_[^_]+_)|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyPrefix}-i${i++}`;
    if (m[1]) {
      out.push(
        <code
          key={key}
          style={{
            fontFamily: "var(--mono)",
            fontSize: "0.88em",
            background: "var(--bg2)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            padding: "1px 5px",
          }}
        >
          {m[1].slice(1, -1)}
        </code>,
      );
    } else if (m[2]) {
      out.push(mathNode(m[2].slice(1, -1), false, key));
    } else if (m[3]) {
      out.push(
        <strong key={key} style={{ color: "var(--hi)", fontWeight: 700 }}>
          {inline(m[3].slice(2, -2), key)}
        </strong>,
      );
    } else if (m[4]) {
      out.push(
        <em key={key}>{inline(m[4].slice(1, -1), key)}</em>,
      );
    } else if (m[5] && m[6]) {
      out.push(
        <a
          key={key}
          href={m[6]}
          target="_blank"
          rel="noreferrer"
          style={{ color: "var(--accent)", textDecoration: "underline" }}
        >
          {m[5]}
        </a>,
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ---- block-level ----

function splitRow(line: string): string[] {
  // "| a | b |" -> ["a", "b"]. Tolerates missing leading/trailing pipes.
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

const isDivider = (line: string) => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes("-");

export default function Markdown({ text, style }: { text: string; style?: React.CSSProperties }) {
  const lines = (text ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;

  while (i < lines.length) {
    const line = lines[i];

    // blank
    if (!line.trim()) {
      i++;
      continue;
    }

    // fenced code
    if (/^\s*```/.test(line)) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; // closing fence
      blocks.push(
        <pre
          key={`b${k++}`}
          style={{
            margin: "8px 0",
            padding: "10px 12px",
            background: "var(--bg2)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontFamily: "var(--mono)",
            fontSize: "0.85em",
            lineHeight: 1.5,
            overflowX: "auto",
          }}
        >
          {buf.join("\n")}
        </pre>,
      );
      continue;
    }

    // display math ($$…$$) — a block, on one line or fenced across lines
    if (/^\s*\$\$/.test(line)) {
      const trimmed = line.trim();
      const oneLine = /^\$\$([\s\S]+?)\$\$$/.exec(trimmed);
      if (oneLine) {
        blocks.push(mathNode(oneLine[1].trim(), true, `b${k++}`));
        i++;
        continue;
      }
      const buf: string[] = [];
      const afterOpen = trimmed.replace(/^\$\$/, "");
      if (afterOpen) buf.push(afterOpen);
      i++;
      while (i < lines.length && !lines[i].includes("$$")) buf.push(lines[i++]);
      if (i < lines.length) {
        const beforeClose = lines[i].replace(/\$\$.*$/, "");
        if (beforeClose.trim()) buf.push(beforeClose);
        i++; // consume the closing line
      }
      blocks.push(mathNode(buf.join("\n").trim(), true, `b${k++}`));
      continue;
    }

    // horizontal rule
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      blocks.push(<hr key={`b${k++}`} style={{ border: 0, borderTop: "1px solid var(--border)", margin: "12px 0" }} />);
      i++;
      continue;
    }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const size = [1.35, 1.2, 1.08, 1, 0.95, 0.9][level - 1];
      blocks.push(
        <div
          key={`b${k++}`}
          style={{
            fontSize: `${size}em`,
            fontWeight: 700,
            color: "var(--hi)",
            margin: level <= 2 ? "14px 0 6px" : "10px 0 4px",
            lineHeight: 1.3,
          }}
        >
          {inline(h[2], `b${k}`)}
        </div>,
      );
      i++;
      continue;
    }

    // table: header row followed by a divider row
    if (line.includes("|") && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const header = splitRow(line);
      i += 2; // header + divider
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={`b${k++}`} style={{ overflowX: "auto", margin: "8px 0" }}>
          <table style={{ borderCollapse: "collapse", fontSize: "0.9em", width: "100%" }}>
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th
                    key={ci}
                    style={{
                      textAlign: "left",
                      padding: "5px 9px",
                      borderBottom: "1px solid var(--border-hi)",
                      color: "var(--hi)",
                      fontWeight: 700,
                    }}
                  >
                    {inline(c, `th${ci}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci} style={{ padding: "5px 9px", borderBottom: "1px solid var(--border)", color: "var(--mid)" }}>
                      {inline(c, `td${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote
          key={`b${k++}`}
          style={{ margin: "8px 0", padding: "2px 12px", borderLeft: "3px solid var(--border-hi)", color: "var(--mid)" }}
        >
          {inline(buf.join(" "), `b${k}`)}
        </blockquote>,
      );
      continue;
    }

    // lists (ordered / unordered) — consume a contiguous run
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        i++;
      }
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={`b${k++}`} style={{ margin: "6px 0", paddingLeft: 22, color: "var(--hi)", lineHeight: 1.55 }}>
          {items.map((it, ii) => (
            <li key={ii} style={{ margin: "2px 0" }}>
              {inline(it, `b${k}-${ii}`)}
            </li>
          ))}
        </Tag>,
      );
      continue;
    }

    // paragraph — merge consecutive non-blank, non-structural lines
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*(#{1,6}\s|```|\$\$|>|\s*([-*+]|\d+\.)\s|(---|\*\*\*|___)\s*$)/.test(lines[i]) &&
      !(lines[i].includes("|") && i + 1 < lines.length && isDivider(lines[i + 1]))
    ) {
      buf.push(lines[i]);
      i++;
    }
    if (buf.length) {
      blocks.push(
        <p key={`b${k++}`} style={{ margin: "6px 0", color: "var(--hi)", lineHeight: 1.55 }}>
          {inline(buf.join("\n"), `b${k}`).map((n, ni) => (
            <Fragment key={ni}>{n}</Fragment>
          ))}
        </p>,
      );
    } else {
      i++; // safety: never spin
    }
  }

  return <div style={style}>{blocks}</div>;
}
