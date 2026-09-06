"""Onboarding phase (O1): the fixed-format problem brief.

Before a research session exists, the human defines the problem in a fixed
structure, attaches the data it needs, and runs the hypothesis search. Launch
freezes the brief into the session and hands the supervisor the whole thing as
its first turn. See docs/plans/O1-onboarding.md.

This module is pure logic (store paths, rendering, validation). The HTTP routes
live in api/server.py next to the other route groups.

Why the brief is composed here and passed as the first-turn *message* rather
than rendered into the supervisor's system prompt: every tenant runs a frozen
copy of research/ + scaffold/ (api/tenancy.py copies them once, and the
evolution agent may have modified them since). A runtime change would not reach
existing users; the API is shared by all of them and is always current.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scaffold._atomic import read_json, write_json


BRIEF_FILENAME = "brief.json"  # frozen copy written into the session dir at launch

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
MAX_TEXT_CHARS = 60_000  # per free-text field; the whole message is capped separately

# The canonical section order. Every brief renders in exactly this order so the
# supervisor always sees the same shape regardless of which fields are filled.
REQUIRED_FIELDS = ("title", "research_question")

_TEXT_FIELDS = (
    "title",
    "domain",
    "background",
    "research_question",
    "data_notes",
    "constraints",
    "success_criteria",
)
_LIST_FIELDS = ("objectives", "deliverables")


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
    return rec if isinstance(rec, dict) and rec.get("id") == bid else None


def save_brief(state: Path, rec: dict) -> dict:
    rec["updated"] = now_str()
    write_json(brief_path(state, rec["id"]), rec)
    return rec


def list_briefs(state: Path) -> list[dict]:
    out: list[dict] = []
    for p in brief_dir(state).glob("*.json"):
        rec = read_json(p, None)
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    out.sort(key=lambda r: r.get("updated") or r.get("created") or "", reverse=True)
    return out


def empty_brief() -> dict:
    """A brief with every field present (fixed shape), all empty."""
    rec: dict[str, Any] = {f: "" for f in _TEXT_FIELDS}
    for f in _LIST_FIELDS:
        rec[f] = []
    rec["data"] = []
    rec["hypothesis"] = None
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


def merge_fields(rec: dict, fields: dict) -> dict:
    """Apply a partial update. Only the brief's own fields are writable here —
    bookkeeping (id/status/session_id/hypothesis) is set by the server."""
    for f in _TEXT_FIELDS:
        if f in fields and fields[f] is not None:
            val = str(fields[f]).strip()
            if len(val) > MAX_TEXT_CHARS:
                raise ValueError(f"{f} is too long ({len(val)} chars > {MAX_TEXT_CHARS})")
            rec[f] = val
    for f in _LIST_FIELDS:
        if f in fields and fields[f] is not None:
            items = [str(x).strip() for x in fields[f] if str(x).strip()]
            if len(items) > MAX_LIST_ITEMS:
                raise ValueError(f"{f} has too many items ({len(items)} > {MAX_LIST_ITEMS})")
            rec[f] = items
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
        "status": rec.get("status", "draft"),
        "created": rec.get("created"),
        "updated": rec.get("updated"),
        "session_id": rec.get("session_id"),
        "hypothesis_session_id": rec.get("hypothesis_session_id"),
        "hypothesis": hyp.get("statement") if isinstance(hyp, dict) else None,
        "data_count": len(rec.get("data") or []),
    }


# ---------- validation ----------


def missing_required(rec: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not (rec.get(f) or "").strip()]


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
    notes = (rec.get("data_notes") or "").strip()
    if notes:
        lines.append("")
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


def render_brief(rec: dict, library_root: Path | None = None) -> str:
    """Render the brief in the canonical fixed format (Markdown)."""
    title = (rec.get("title") or "").strip() or "(untitled)"
    lines = [f"# Problem brief: {title}"]
    domain = (rec.get("domain") or "").strip()
    if domain:
        lines.append(f"**Domain:** {domain}")
    lines += [
        "",
        "## Background",
        _para(rec.get("background", ""), "No background given."),
        "",
        "## Research question",
        _para(rec.get("research_question", ""), "No research question given."),
        "",
        "## Objectives",
        _numbered(rec.get("objectives") or [], "No explicit objectives — derive them from the research question."),
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
        "## Working hypothesis",
        render_hypothesis_section(rec),
    ]
    return "\n".join(lines).rstrip() + "\n"


def opening_message(rec: dict, library_root: Path | None = None) -> str:
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
        + render_brief(rec, library_root)
        + "\n[How to proceed] Start by confirming your reading of the brief in a few "
        "sentences (what you will answer, with which data, and which hypothesis you "
        "are testing or developing), then plan. Work toward the research question; "
        "treat the success criteria and deliverables as the definition of done; "
        "surface anything in the brief that is ambiguous or missing via `hitl.ask` "
        "rather than guessing.\n"
    )


def brief_goal(rec: dict) -> tuple[str, str]:
    """(goal, context) for seeding the hypothesis search from the brief.

    The goal is the research question (falling back to the title); the context
    is everything else the generator should respect so the candidates are
    grounded in this problem rather than a one-line prompt."""
    goal = (rec.get("research_question") or "").strip() or (rec.get("title") or "").strip()
    parts: list[str] = []
    if (rec.get("title") or "").strip():
        parts.append(f"Title: {rec['title'].strip()}")
    if (rec.get("domain") or "").strip():
        parts.append(f"Domain: {rec['domain'].strip()}")
    if (rec.get("background") or "").strip():
        parts.append(f"Background: {rec['background'].strip()}")
    objs = [o for o in (rec.get("objectives") or []) if o.strip()]
    if objs:
        parts.append("Objectives:\n" + "\n".join(f"- {o}" for o in objs))
    data = rec.get("data") or []
    if data:
        parts.append(
            "Available data:\n"
            + "\n".join(
                f"- {d.get('path')}" + (f": {d['description']}" if d.get("description") else "")
                for d in data
            )
        )
    if (rec.get("data_notes") or "").strip():
        parts.append(f"Data notes: {rec['data_notes'].strip()}")
    if (rec.get("constraints") or "").strip():
        parts.append(f"Constraints: {rec['constraints'].strip()}")
    if (rec.get("success_criteria") or "").strip():
        parts.append(f"Success criteria: {rec['success_criteria'].strip()}")
    return goal, "\n\n".join(parts)
