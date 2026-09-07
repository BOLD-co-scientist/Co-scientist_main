"""Onboarding phase: the fixed-format problem brief (O1 → O2 "brief v2").

Before a research session exists, the human defines the problem in a fixed
structure — the five questions we ask every researcher — attaches the data it
needs, registers the problem on the org-wide project tree (O2), reads the
background advisor's recommendations, optionally runs the hypothesis search,
and launches. Launch freezes the brief into the session and hands the
supervisor the whole thing as its first turn.
See docs/plans/O1-onboarding.md and docs/plans/O2-project-tree-advisor.md.

This module is pure logic (store paths, rendering, validation). The HTTP routes
live in api/server.py next to the other route groups; the registry is
api/projects.py.

Why the brief is composed here and passed as the first-turn *message* rather
than rendered into the supervisor's system prompt: every tenant runs a frozen
copy of research/ + scaffold/ (api/tenancy.py copies them once, and the
evolution agent may have modified them since). A runtime change would not reach
existing users; the API is shared by all of them and is always current.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scaffold._atomic import read_json, write_json


BRIEF_FILENAME = "brief.json"  # frozen copy written into the session dir at launch
SCHEMA_VERSION = 2  # brief v2: the five-question statement (additive over O1)

# The opening message travels to the runtime as ONE argv element (`--task`).
# Linux caps a single argv string at MAX_ARG_STRLEN = 128 KiB; exceeding it
# makes the spawn fail with E2BIG. Keep a safety margin so the brief (UTF-8
# bytes, not characters) always fits, on every tenant.
OPENING_MESSAGE_MAX_BYTES = 100_000

# Attached files above this size get an explicit "don't read it whole" note.
LARGE_FILE_BYTES = 50 * 1024 * 1024
# Extensions the text tools cannot read meaningfully; steer the agent to py_exec/ocr.
_BINARY_EXTS = {
    ".xlsx", ".xls", ".parquet", ".feather", ".h5", ".hdf5", ".npy", ".npz", ".pkl",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".pdf", ".zip", ".gz", ".tar",
    ".bam", ".fastq.gz", ".sdf", ".mol2", ".pdb.gz",
}
# How many entries of an attached folder to list inline.
FOLDER_LISTING_LIMIT = 20
# Attachments are resolved (rglob for folders) on every preview/launch; keep
# the list bounded and de-duplicated.
MAX_DATA_ITEMS = 200
MAX_LIST_ITEMS = 200  # objectives / deliverables
MAX_KEYWORDS = 30
MAX_KEYWORD_CHARS = 60
MAX_TEXT_CHARS = 60_000  # per free-text field; the whole message is capped separately

# Required to LAUNCH (O1 rule, unchanged).
REQUIRED_FIELDS = ("title", "research_question")
# Required to REGISTER on the project tree / share with the org (O2): the
# definition plus questions 1–3. Question 4 (data) is advised, never blocking.
REGISTRATION_FIELDS = (
    "title",
    "research_question",
    "significance",
    "prior_work",
    "open_gap",
    "evaluation_protocol",
)
VISIBILITIES = ("org", "private")

# The five questions, keyed to the fields that answer them. Shared with the UI
# (it mirrors this table) and with the advisor's "statement review" so gaps are
# named by question number everywhere.
QUESTIONS: tuple[dict[str, Any], ...] = (
    {"q": 0, "label": "Definition of the problem", "fields": ("title", "research_question", "objectives", "domain")},
    {"q": 1, "label": "Why the problem is scientifically important", "fields": ("significance",)},
    {"q": 2, "label": "What existing work has achieved, and what remains genuinely open", "fields": ("prior_work", "open_gap")},
    {"q": 3, "label": "How progress is evaluated objectively — no hackable proxy, shortcut or subjective judgement", "fields": ("evaluation_protocol", "success_criteria")},
    {"q": 4, "label": "The exact dataset, metadata, task definition, evaluation protocol, existing results and permissions", "fields": ("data", "task_definition", "existing_results", "data_access", "data_notes")},
)

_TEXT_FIELDS = (
    "title",
    "domain",
    "research_question",
    "significance",  # Q1
    "prior_work",  # Q2 — absorbs the O1 `background`
    "open_gap",  # Q2
    "evaluation_protocol",  # Q3
    "task_definition",  # Q4
    "existing_results",  # Q4
    "data_access",  # Q4
    "data_notes",  # Q4
    "constraints",
    "success_criteria",
    "background",  # O1 alias: still writable so old clients keep working
)
_LIST_FIELDS = ("objectives", "deliverables", "keywords")
# Fields that make up the shareable *statement* (the projection registered on
# the project tree). Everything else (constraints, deliverables, hypothesis) is
# session-private.
STATEMENT_FIELDS = (
    "title",
    "domain",
    "research_question",
    "objectives",
    "significance",
    "prior_work",
    "open_gap",
    "evaluation_protocol",
    "success_criteria",
    "task_definition",
    "existing_results",
    "data_access",
    "data_notes",
    "keywords",
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_brief_id() -> str:
    return "brief-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


# ---------- store ----------


def brief_dir(state: Path) -> Path:
    d = state / "onboarding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def brief_path(state: Path, bid: str) -> Path:
    return brief_dir(state) / f"{bid}.json"


def load_brief(state: Path, bid: str) -> dict | None:
    rec = read_json(brief_path(state, bid), None)
    if not (isinstance(rec, dict) and rec.get("id") == bid):
        return None
    return upgrade(rec)


def save_brief(state: Path, rec: dict) -> dict:
    rec["updated"] = now_str()
    write_json(brief_path(state, rec["id"]), rec)
    return rec


def list_briefs(state: Path) -> list[dict]:
    out: list[dict] = []
    for p in brief_dir(state).glob("*.json"):
        try:
            rec = read_json(p, None)
        except Exception:
            continue  # one corrupt draft must not hide the rest
        if isinstance(rec, dict) and rec.get("id"):
            out.append(upgrade(rec))
    out.sort(key=lambda r: r.get("updated") or r.get("created") or "", reverse=True)
    return out


def empty_brief() -> dict:
    """A brief with every field present (fixed shape), all empty."""
    rec: dict[str, Any] = {f: "" for f in _TEXT_FIELDS}
    for f in _LIST_FIELDS:
        rec[f] = []
    rec["data"] = []
    rec["hypothesis"] = None
    rec["visibility"] = "org"
    rec["parent_node"] = None
    rec["node_id"] = None  # set by the server when registered on the project tree
    rec["harness"] = None  # stamped by the server at launch (active version + tools)
    rec["tree_context"] = None  # stamped by the server at launch (rendered section)
    rec["schema_version"] = SCHEMA_VERSION
    return rec


def upgrade(rec: dict) -> dict:
    """Bring an O1-era record (or a partial one) up to the v2 shape in memory.
    Additive only: missing keys get their empty value; `background` is read
    into `prior_work` when the latter is empty. Old records are NOT rewritten
    on disk until the next save."""
    base = empty_brief()
    for k, v in base.items():
        if k not in rec or rec[k] is None and k not in ("hypothesis", "parent_node", "node_id", "harness", "tree_context"):
            rec[k] = v
    if not (rec.get("prior_work") or "").strip() and (rec.get("background") or "").strip():
        rec["prior_work"] = rec["background"]
    if rec.get("visibility") not in VISIBILITIES:
        rec["visibility"] = "org"
    rec["schema_version"] = SCHEMA_VERSION
    return rec


def new_record(fields: dict) -> dict:
    rec = empty_brief()
    merge_fields(rec, fields)
    ts = now_str()
    rec.update(
        {
            "id": new_brief_id(),
            "status": "draft",
            "created": ts,
            "updated": ts,
            "hypothesis_session_id": None,
            "session_id": None,
        }
    )
    return rec


def _clean_keywords(items: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items or []:
        k = " ".join(str(x).split()).strip(" ,;")
        if not k:
            continue
        if len(k) > MAX_KEYWORD_CHARS:
            k = k[:MAX_KEYWORD_CHARS]
        key = k.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(k)
    if len(out) > MAX_KEYWORDS:
        raise ValueError(f"too many keywords ({len(out)} > {MAX_KEYWORDS})")
    return out


def merge_fields(rec: dict, fields: dict) -> dict:
    """Apply a partial update. Only the brief's own fields are writable here —
    bookkeeping (id/status/session_id/hypothesis/node_id/harness) is set by
    the server."""
    for f in _TEXT_FIELDS:
        if f in fields and fields[f] is not None:
            val = str(fields[f]).strip()
            if len(val) > MAX_TEXT_CHARS:
                raise ValueError(f"{f} is too long ({len(val)} chars > {MAX_TEXT_CHARS})")
            rec[f] = val
    # O1 alias: writing `background` on a v2 record fills `prior_work` unless
    # the client also sent prior_work explicitly.
    if "background" in fields and fields["background"] is not None and "prior_work" not in fields:
        rec["prior_work"] = rec["background"]
    for f in ("objectives", "deliverables"):
        if f in fields and fields[f] is not None:
            items = [str(x).strip() for x in fields[f] if str(x).strip()]
            if len(items) > MAX_LIST_ITEMS:
                raise ValueError(f"{f} has too many items ({len(items)} > {MAX_LIST_ITEMS})")
            rec[f] = items
    if "keywords" in fields and fields["keywords"] is not None:
        rec["keywords"] = _clean_keywords(fields["keywords"])
    if "visibility" in fields and fields["visibility"] is not None:
        vis = str(fields["visibility"]).strip().lower()
        if vis not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {', '.join(VISIBILITIES)}")
        rec["visibility"] = vis
    if "parent_node" in fields:
        pn = fields["parent_node"]
        rec["parent_node"] = (str(pn).strip() or None) if pn is not None else None
    if "data" in fields and fields["data"] is not None:
        items = []
        seen: set[str] = set()
        for item in fields["data"]:
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip().strip("/")
                if not path or path in seen:
                    continue  # blank or duplicate attachment
                seen.add(path)
                items.append({"path": path, "description": str(item.get("description", "")).strip()})
        if len(items) > MAX_DATA_ITEMS:
            raise ValueError(f"too many data attachments ({len(items)} > {MAX_DATA_ITEMS}); attach a folder instead")
        rec["data"] = items
    return rec


def summary(rec: dict) -> dict:
    hyp = rec.get("hypothesis") or None
    return {
        "id": rec.get("id"),
        "title": rec.get("title") or "",
        "research_question": rec.get("research_question") or "",
        "domain": rec.get("domain") or "",
        "keywords": list(rec.get("keywords") or []),
        "status": rec.get("status", "draft"),
        "created": rec.get("created"),
        "updated": rec.get("updated"),
        "session_id": rec.get("session_id"),
        "hypothesis_session_id": rec.get("hypothesis_session_id"),
        "hypothesis": hyp.get("statement") if isinstance(hyp, dict) else None,
        "data_count": len(rec.get("data") or []),
        "visibility": rec.get("visibility") or "org",
        "node_id": rec.get("node_id"),
        "parent_node": rec.get("parent_node"),
        "registrable": not missing_for_registration(rec),
        "completeness": completeness(rec),
    }


# ---------- validation ----------


def missing_required(rec: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not (rec.get(f) or "").strip()]


def missing_for_registration(rec: dict) -> list[str]:
    return [f for f in REGISTRATION_FIELDS if not (rec.get(f) or "").strip()]


def completeness(rec: dict) -> list[dict]:
    """Per-question completeness for the UI's dots and the advisor's review:
    [{q, label, filled, missing[]}]."""
    out: list[dict] = []
    for q in QUESTIONS:
        missing: list[str] = []
        for f in q["fields"]:
            v = rec.get(f)
            if isinstance(v, list):
                if not v and f in ("data",):
                    continue  # data is optional; the question still counts as answered
                if not v and f == "objectives":
                    continue  # objectives are derived from the question if absent
                if not v:
                    missing.append(f)
            elif not (v or "").strip():
                if f in ("domain", "success_criteria", "data_notes"):
                    continue  # optional companions
                missing.append(f)
        out.append({"q": q["q"], "label": q["label"], "filled": not missing, "missing": missing})
    return out


# ---------- statement projection (what the project tree stores) ----------


def statement(rec: dict) -> dict:
    """The shareable projection of a brief: the five-question statement plus
    the *names* of attached data (never contents)."""
    st: dict[str, Any] = {}
    for f in STATEMENT_FIELDS:
        v = rec.get(f)
        if isinstance(v, list):
            st[f] = [str(x) for x in v]
        else:
            st[f] = (v or "").strip() if isinstance(v, str) else ""
    if not st.get("prior_work") and (rec.get("background") or "").strip():
        st["prior_work"] = rec["background"].strip()
    st["data"] = [
        {"path": d.get("path", ""), "description": d.get("description", "")}
        for d in (rec.get("data") or [])
        if isinstance(d, dict) and d.get("path")
    ]
    return st


def statement_sha(st: dict) -> str:
    return hashlib.sha256(json.dumps(st, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------- rendering (the fixed format) ----------


def _fmt_size(n: int | None) -> str:
    if n is None:
        return ""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)}{unit}" if unit == "B" else f"{v:.1f}{unit}"
        v /= 1024
    return f"{v:.1f}TB"


def _para(text: str, empty: str) -> str:
    t = (text or "").strip()
    return t if t else f"_{empty}_"


def _numbered(items: list[str], empty: str) -> str:
    items = [i for i in (items or []) if i.strip()]
    if not items:
        return f"_{empty}_"
    return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))


def _is_binary_name(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(ext) for ext in _BINARY_EXTS)


def render_data_section(rec: dict, library_root: Path | None) -> str:
    items = rec.get("data") or []
    lines: list[str] = []
    if not items:
        lines.append(
            "_No data files were attached to this brief._ If the task needs data, "
            "check `state/library/` and `researcher_data/` with `fs_read.list`, or ask the human."
        )
    has_folder = False
    for item in items:
        rel = item.get("path", "")
        desc = (item.get("description") or "").strip()
        size = item.get("size")
        is_dir = bool(item.get("is_dir"))
        loc = f"`state/library/{rel}{'/' if is_dir else ''}`"
        if library_root is not None:
            loc += f" (absolute: `{library_root / rel}`)"
        head = f"- {loc}"
        if is_dir:
            has_folder = True
            n = item.get("file_count")
            head += f" · folder, {n} file{'s' if n != 1 else ''}" if isinstance(n, int) else " · folder"
            if size is not None:
                head += f", {_fmt_size(size)} total"
        elif size is not None:
            head += f" · {_fmt_size(size)}"
        if desc:
            head += f" — {desc}"
        lines.append(head)
        # Folders: a bounded inner listing so the agent knows what is inside
        # without a first exploratory tool call.
        files = item.get("files") or []
        if is_dir and files:
            for f in files[:FOLDER_LISTING_LIMIT]:
                lines.append(f"    - `{f}`")
            extra = (item.get("file_count") or len(files)) - min(len(files), FOLDER_LISTING_LIMIT)
            if isinstance(extra, int) and extra > 0:
                lines.append(f"    - … {extra} more (use `fs_read.list` to see them)")
        # Warnings that save a wasted or fatal first tool call.
        if not is_dir and isinstance(size, int) and size > LARGE_FILE_BYTES:
            lines.append(
                f"    - ⚠ large ({_fmt_size(size)}): do not `fs_read.read` it whole — sample it "
                "with `py_exec` (chunked reads) or route heavy processing via `longjob`."
            )
        if not is_dir and _is_binary_name(rel):
            lines.append(
                "    - ⚠ binary format: `fs_read.read` returns garbage for this; open it with "
                "`py_exec` (or `ocr` for images/PDFs)."
            )
    if items:
        lines.append("")
        lines.append(
            "Read files with `fs_read.read` (the `state/library/...` path) or, in "
            "`py_exec`, open the absolute path"
            + (
                "; folders are listed with `fs_read.list` (one level at a time) — "
                "`fs_read.read` works on files only"
                if has_folder
                else ""
            )
            + ". They are read-only; write outputs to the session's `results/`. If "
            "`import pandas` fails inside `py_exec`, fall back to the `csv` stdlib "
            "module or `longjob`."
        )
    # Question 4 companions: always present so the shape is fixed.
    lines.append("")
    lines.append("**Task definition:** " + _para(rec.get("task_definition", ""), "Not stated — derive the task from the research question and evaluation protocol."))
    lines.append("**Existing results / baselines:** " + _para(rec.get("existing_results", ""), "None recorded."))
    lines.append("**Data access and permissions:** " + _para(rec.get("data_access", ""), "None stated — assume the attached files may be used for this analysis only."))
    notes = (rec.get("data_notes") or "").strip()
    if notes:
        lines.append(f"**Data notes:** {notes}")
    return "\n".join(lines)


def message_size(rec: dict, library_root: Path | None = None) -> int:
    """UTF-8 byte length of the opening message (the argv element)."""
    return len(opening_message(rec, library_root).encode("utf-8"))


def render_hypothesis_section(rec: dict) -> str:
    hyp = rec.get("hypothesis")
    if not isinstance(hyp, dict) or not (hyp.get("statement") or "").strip():
        return (
            "_No working hypothesis was selected during onboarding._ Propose your own "
            "candidate hypotheses early, state them explicitly, and let the human choose."
        )
    out = [f"**Statement:** {hyp['statement'].strip()}"]
    if (hyp.get("rationale") or "").strip():
        out.append(f"**Rationale:** {hyp['rationale'].strip()}")
    origin = []
    if hyp.get("id"):
        origin.append(f"id {hyp['id']}")
    if rec.get("hypothesis_session_id"):
        origin.append(f"hypothesis search {rec['hypothesis_session_id']}")
    if origin:
        out.append(f"_Selected by the human during onboarding ({', '.join(origin)})._")
    if (hyp.get("note") or "").strip():
        out.append(f"**Human's note on this choice:** {hyp['note'].strip()}")
    return "\n".join(out)


def render_harness_section(rec: dict) -> str:
    """The harness the session runs on (stamped at launch): active version,
    provenance if it was imported from a colleague, and the tool inventory."""
    h = rec.get("harness")
    if not isinstance(h, dict):
        return "_Not stamped (preview) — the active harness version is recorded at launch._"
    lines: list[str] = []
    vid = h.get("version_id")
    if vid:
        summ = (h.get("summary") or "").strip()
        sha = (h.get("sha") or "")[:8]
        lines.append(f"**Active version:** {summ or vid} (`{vid}`" + (f", {sha}" if sha else "") + ")")
    else:
        lines.append("**Active version:** bootstrap harness (no evolved version active)" + (f" — {h.get('sha', '')[:8]}" if h.get("sha") else ""))
    imp = h.get("imported_from")
    if isinstance(imp, dict) and imp.get("version_id"):
        who = imp.get("owner_name") or imp.get("owner") or "a colleague"
        lines.append(f"**Imported from:** {who}'s version `{imp['version_id']}`" + (f" — {imp.get('summary')}" if imp.get("summary") else "") + ". Prefer the tools and roles that version added; they were evolved for an adjacent problem.")
    tools = [t for t in (h.get("tools") or []) if t]
    if tools:
        lines.append("**Tools available:** " + ", ".join(f"`{t}`" for t in tools))
    custom = [t for t in (h.get("custom_tools") or []) if t]
    if custom:
        lines.append("**Evolved (non-base) tools:** " + ", ".join(f"`{t}`" for t in custom) + " — read their descriptions before planning; they exist because a previous problem needed them.")
    roles = [r for r in (h.get("roles") or []) if r]
    if roles:
        lines.append("**Roles:** " + ", ".join(roles))
    return "\n".join(lines)


def render_brief(rec: dict, library_root: Path | None = None, *, tree_context: str | None = None) -> str:
    """Render the brief in the canonical fixed format (Markdown).

    The section order is fixed for every brief, so the supervisor always
    finds the same headings whatever was filled in. `tree_context` (server
    composed) overrides the copy stamped on the record at launch."""
    rec = upgrade(dict(rec))
    title = (rec.get("title") or "").strip() or "(untitled)"
    lines = [f"# Problem brief: {title}"]
    domain = (rec.get("domain") or "").strip()
    if domain:
        lines.append(f"**Domain:** {domain}")
    kws = [k for k in (rec.get("keywords") or []) if k]
    if kws:
        lines.append("**Keywords:** " + ", ".join(kws))
    prior = (rec.get("prior_work") or "").strip() or (rec.get("background") or "").strip()
    ctx_text = (tree_context if tree_context is not None else rec.get("tree_context")) or ""
    lines += [
        "",
        "## Why this matters",
        _para(rec.get("significance", ""), "Not stated — infer the scientific stakes from the question and say them back to the human."),
        "",
        "## Prior work and what remains open",
        "**What existing work has achieved:** " + _para(prior, "Not stated — establish the state of the art before proposing anything new."),
        "**What remains genuinely open:** " + _para(rec.get("open_gap", ""), "Not stated — name the open question explicitly in your first reply."),
        "",
        "## Research question",
        _para(rec.get("research_question", ""), "No research question given."),
        "",
        "## Objectives",
        _numbered(rec.get("objectives") or [], "No explicit objectives — derive them from the research question."),
        "",
        "## Evaluation protocol",
        _para(rec.get("evaluation_protocol", ""), "Not stated — propose an objective, non-hackable protocol and confirm it with the human before optimising anything."),
        "",
        "## Data",
        render_data_section(rec, library_root),
        "",
        "## Constraints",
        _para(rec.get("constraints", ""), "None stated."),
        "",
        "## Success criteria",
        _para(rec.get("success_criteria", ""), "None stated — propose what a convincing answer would look like and confirm it with the human."),
        "",
        "## Deliverables",
        _numbered(rec.get("deliverables") or [], "None stated — default to a written report under results/."),
        "",
        "## Project tree context",
        ctx_text.strip() if ctx_text.strip() else "_This problem is not registered on the project tree, or has no adjacent problems yet._",
        "",
        "## Harness",
        render_harness_section(rec),
        "",
        "## Working hypothesis",
        render_hypothesis_section(rec),
    ]
    return "\n".join(lines).rstrip() + "\n"


def opening_message(rec: dict, library_root: Path | None = None, *, tree_context: str | None = None) -> str:
    """The supervisor's first-turn message: the brief plus how to use it.

    Note what the runtime does with it (unchanged, on every tenant): it is
    rendered into the supervisor's system prompt as ``{{TASK}}`` *and* sent as
    the first user turn, prefixed by the runtime's own library listing. So the
    agent sees the brief twice; that is pre-existing behaviour, not something
    to "fix" here."""
    return (
        "[Problem brief — prepared by the researcher during onboarding. It is the "
        "authoritative definition of this session's problem; the short task line "
        "elsewhere is just its title.]\n\n"
        + render_brief(rec, library_root, tree_context=tree_context)
        + "\n[How to proceed] Start by confirming your reading of the brief in a few "
        "sentences (what you will answer, with which data, on which harness version, "
        "and which hypothesis you are testing or developing), then plan. Work toward "
        "the research question; measure progress ONLY by the evaluation protocol — "
        "never substitute a proxy metric or a subjective judgement; treat the success "
        "criteria and deliverables as the definition of done; reuse what the adjacent "
        "problems in the project tree already established instead of repeating it; "
        "surface anything in the brief that is ambiguous or missing via `hitl.ask` "
        "rather than guessing.\n"
    )


def brief_goal(rec: dict) -> tuple[str, str]:
    """(goal, context) for seeding the hypothesis search from the brief.

    The goal is the research question (falling back to the title); the context
    is everything else the generator should respect so the candidates are
    grounded in this problem rather than a one-line prompt."""
    rec = upgrade(dict(rec))
    goal = (rec.get("research_question") or "").strip() or (rec.get("title") or "").strip()
    parts: list[str] = []
    if (rec.get("title") or "").strip():
        parts.append(f"Title: {rec['title'].strip()}")
    if (rec.get("domain") or "").strip():
        parts.append(f"Domain: {rec['domain'].strip()}")
    if (rec.get("significance") or "").strip():
        parts.append(f"Why it matters: {rec['significance'].strip()}")
    prior = (rec.get("prior_work") or "").strip() or (rec.get("background") or "").strip()
    if prior:
        parts.append(f"Prior work: {prior}")
    if (rec.get("open_gap") or "").strip():
        parts.append(f"What remains open: {rec['open_gap'].strip()}")
    objs = [o for o in (rec.get("objectives") or []) if o.strip()]
    if objs:
        parts.append("Objectives:\n" + "\n".join(f"- {o}" for o in objs))
    if (rec.get("evaluation_protocol") or "").strip():
        parts.append(f"Evaluation protocol (hypotheses must be testable under it): {rec['evaluation_protocol'].strip()}")
    data = rec.get("data") or []
    if data:
        parts.append(
            "Available data:\n"
            + "\n".join(
                f"- {d.get('path')}" + (f": {d['description']}" if d.get("description") else "")
                for d in data
            )
        )
    for f, label in (("task_definition", "Task definition"), ("existing_results", "Existing results"), ("data_notes", "Data notes"), ("constraints", "Constraints"), ("success_criteria", "Success criteria")):
        if (rec.get(f) or "").strip():
            parts.append(f"{label}: {rec[f].strip()}")
    return goal, "\n\n".join(parts)
