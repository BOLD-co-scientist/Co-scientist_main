"""The org-wide project tree (O2): a platform-owned registry of research
problems, how they relate, and what each one used (sessions, harness versions,
tools, datasets, outcomes).

Lives OUTSIDE every tenant root, under ``state/projects/``:

    nodes/<pid>.json          one per registered problem (statement projection + links)
    edges.jsonl               append-only; the current edge = last record per (src, dst, type)
    recommendations/<pid>/    advisor records (api/advisor.py)
    imports/<iid>.json        cross-tenant import ledger (api/imports.py)
    events.jsonl              node.* / edge.* / advisor.* / import.*

Cross-tenant data is read ONLY by this platform code and surfaced as bounded
summaries: statements, owner names, statuses, outcome one-liners, version
summaries and tool names. Never another tenant's files, events, memory,
hypotheses or HITL records. Visibility (``org`` | ``private``) is filtered
server-side on every read. See docs/plans/O2-project-tree-advisor.md.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import math
from collections import Counter

import yaml

from scaffold import archive, settings
from scaffold._atomic import append_jsonl, read_json, write_json

from . import onboarding as _onboarding
from .tenancy import UserContext, USERS_DIR, user_root


NODE_STATUSES = ("registered", "active", "solved", "abandoned", "superseded")
EDGE_TYPES = ("adjacent", "subproblem", "shares_dataset", "shares_method", "supersedes")
EDGE_SOURCES = ("keyword", "advisor", "human", "import")
EDGE_STATUSES = ("proposed", "confirmed", "rejected", "dropped")
SYMMETRIC_TYPES = ("adjacent", "shares_dataset", "shares_method")

ADJACENCY_TOP_K = 8
ADJACENCY_MIN_NORM = 0.12  # of the statement's self-score (see bm25_scores); calibrated on real statements
ADJACENCY_MIN_SHARED_TERMS = 3
OUTCOME_MAX_CHARS = 300
FINGERPRINT_HEAD_BYTES = 1024 * 1024

_STOP = set(
    """a an the and or of to in on for with by from as at is are was were be been being this that these those
    it its into over under between within without via using use used we our their there which what who whom whose
    how why when where than then also can could may might should would will do does did done not no nor but if
    so such each per any all both more most other some same own only very each such will just about above after
    before again further here where both few many much less least one two three new well""".split()
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- paths ----------


def projects_root() -> Path:
    return settings.STATE / "projects"


def nodes_dir() -> Path:
    d = projects_root() / "nodes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def edges_path() -> Path:
    return projects_root() / "edges.jsonl"


def events_path() -> Path:
    return projects_root() / "events.jsonl"


def recs_dir(pid: str) -> Path:
    d = projects_root() / "recommendations" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def imports_dir() -> Path:
    d = projects_root() / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def event(kind: str, **fields: Any) -> str:
    eid = uuid.uuid4().hex[:12]
    append_jsonl(events_path(), {"id": eid, "ts": now_str(), "kind": kind, **fields})
    return eid


def safe_pid(raw: str) -> str | None:
    return raw if re.fullmatch(r"p-[0-9]{8}-[0-9a-f]{6}", raw or "") else None


def new_node_id() -> str:
    return "p-" + time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]


# ---------- nodes ----------


def load_node(pid: str) -> dict | None:
    if not safe_pid(pid):
        return None
    try:
        rec = read_json(nodes_dir() / f"{pid}.json", None)
    except Exception:
        return None
    return rec if isinstance(rec, dict) and rec.get("id") == pid else None


def save_node(node: dict) -> dict:
    node["updated"] = now_str()
    write_json(nodes_dir() / f"{node['id']}.json", node)
    return node


def all_nodes() -> list[dict]:
    out: list[dict] = []
    for p in nodes_dir().glob("p-*.json"):
        try:
            rec = read_json(p, None)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    out.sort(key=lambda n: n.get("created") or "", reverse=True)
    return out


def is_owner(node: dict, uid: str) -> bool:
    return (node.get("owner") or {}).get("user_id") == uid


def visible(node: dict, uid: str) -> bool:
    return node.get("visibility") == "org" or is_owner(node, uid)


def node_for_brief(uid: str, brief_id: str) -> dict | None:
    for n in all_nodes():
        src = n.get("source") or {}
        if src.get("user_id") == uid and src.get("brief_id") == brief_id:
            return n
    return None


def node_for_session(uid: str, sid: str) -> dict | None:
    for n in all_nodes():
        if is_owner(n, uid) and sid in ((n.get("links") or {}).get("sessions") or []):
            return n
    return None


# ---------- harness inventory / datasets ----------


def base_tool_names() -> set[str]:
    """Tools that ship with the platform (settings.ROOT/tools). Anything a
    tenant has beyond these was evolved or imported."""
    d = settings.ROOT / "tools"
    if not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))}


def _tool_names_in(root: Path) -> list[str]:
    d = root / "tools"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))


def _role_names_in(root: Path) -> list[str]:
    out: list[str] = []
    if (root / "roles" / "supervisor.yaml").exists():
        out.append("supervisor")
    sub = root / "roles" / "subagents"
    if sub.is_dir():
        out += sorted(p.stem for p in sub.glob("*.yaml"))
    return out


def _role_tools(root: Path) -> dict[str, list[str]]:
    """{role: [tool, ...]} from the tenant's role YAML (which tools each role can call)."""
    out: dict[str, list[str]] = {}
    paths = [root / "roles" / "supervisor.yaml"] + sorted((root / "roles" / "subagents").glob("*.yaml")) if (root / "roles").is_dir() else []
    for p in paths:
        if not p.exists():
            continue
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        name = str(raw.get("name") or p.stem)
        out[name] = [str(t) for t in (raw.get("tools") or [])]
    return out


def harness_inventory(root: Path) -> dict:
    """What a tenant's harness currently is: tools (and which are non-base),
    roles, every switchable version, and the active one. Bounded and cheap."""
    base = base_tool_names()
    tools = _tool_names_in(root)
    try:
        vl = archive.list_versions(root)
    except Exception:
        vl = {"versions": [], "active": None, "head": None}
    versions = []
    for v in vl.get("versions") or []:
        versions.append(
            {
                "id": v.get("id"),
                "summary": (v.get("summary") or "")[:200],
                "sha": v.get("sha"),
                "active": bool(v.get("active")),
                "status": v.get("status"),
                "imported_from": v.get("imported_from"),
                "shared": bool(v.get("shared")),
                "created_at": v.get("created_at"),
            }
        )
    active = next((v for v in versions if v["active"]), None)
    return {
        "tools": tools,
        "custom_tools": [t for t in tools if t not in base],
        "roles": _role_names_in(root),
        "role_tools": _role_tools(root),
        "versions": versions,
        "active_version": active,
        "head": vl.get("head"),
    }


def _inventory_entries(inv: dict) -> list[dict]:
    return [
        {"id": v["id"], "summary": v["summary"], "sha": v["sha"], "active": v["active"], "imported_from": v.get("imported_from")}
        for v in inv["versions"]
    ]


def _refresh_harness_links(node: dict, inv: dict) -> None:
    """Owner-only ``harness_inventory`` = every version the owner has;
    ``links.harness_versions`` = only the versions this problem has RUN on (the
    active one at register/launch/completion). The latter is what colleagues
    see and what makes a version discoverable, so registering one shared
    problem never exposes an owner's whole evolution history."""
    links = node.setdefault("links", {})
    node["harness_inventory"] = _inventory_entries(inv)
    active = inv.get("active_version")
    used = [dict(v, active=False) for v in (links.get("harness_versions") or []) if v.get("id")]
    if active and active.get("id"):
        used = [v for v in used if v["id"] != active["id"]]
        used.append({"id": active["id"], "summary": active.get("summary"), "sha": active.get("sha"), "active": True, "imported_from": active.get("imported_from")})
    links["harness_versions"] = used
    links["tools"], links["custom_tools"], links["roles"] = inv["tools"], inv["custom_tools"], inv["roles"]


def harness_stamp(root: Path) -> dict:
    """The compact `harness` record frozen into a brief at launch."""
    inv = harness_inventory(root)
    act = inv.get("active_version") or {}
    return {
        "version_id": act.get("id"),
        "summary": act.get("summary"),
        "sha": act.get("sha") or inv.get("head"),
        "imported_from": act.get("imported_from"),
        "tools": inv["tools"],
        "custom_tools": inv["custom_tools"],
        "roles": inv["roles"],
    }


def _file_fingerprint(p: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            h.update(f.read(FINGERPRINT_HEAD_BYTES))
    except OSError:
        return "", 0
    return f"{size}:{h.hexdigest()[:16]}", size


def dataset_fingerprints(library: Path, data_items: list[dict]) -> list[dict]:
    """Content fingerprints of the attached data, so two problems that attach
    the same file (under any name) get a `shares_dataset` edge. Never stores
    contents; a folder is fingerprinted by its (relpath, size) listing."""
    out: list[dict] = []
    for item in data_items or []:
        rel = str(item.get("path") or "").strip().strip("/")
        if not rel or ".." in rel.split("/"):
            continue
        target = library / rel
        try:
            if target.is_file():
                fp, size = _file_fingerprint(target)
                if fp:
                    out.append({"path": rel, "fingerprint": fp, "size": size, "kind": "file"})
            elif target.is_dir():
                h = hashlib.sha256()
                total = 0
                for p in sorted(target.rglob("*")):
                    if not p.is_file() or any(seg.startswith(".") for seg in p.relative_to(target).parts):
                        continue
                    st = p.stat()
                    total += st.st_size
                    h.update(f"{p.relative_to(target).as_posix()}:{st.st_size}\n".encode("utf-8"))
                out.append({"path": rel, "fingerprint": f"dir:{h.hexdigest()[:16]}", "size": total, "kind": "folder"})
        except OSError:
            continue
    return out


# ---------- register / update ----------


def _statement_text(st: dict) -> str:
    parts = [st.get("title", ""), st.get("research_question", ""), st.get("significance", ""), st.get("open_gap", ""), st.get("prior_work", ""), st.get("evaluation_protocol", ""), st.get("domain", "")]
    parts += list(st.get("keywords") or []) * 2  # keywords count double
    parts += list(st.get("objectives") or [])
    return "\n".join(p for p in parts if p)


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", (text or "").lower())
    return [t for t in toks if t not in _STOP]


def bm25_scores(query: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """BM25 with the Lucene (always-positive) IDF, normalised by the query's
    score against ITSELF, so a value is comparable across corpora of any size:
    1.0 = the same statement, ~0.2 = a genuinely adjacent problem, ~0.01 =
    unrelated. (rank_bm25's Okapi IDF goes negative on tiny corpora, which is
    exactly the size of an early project tree.)"""
    corpus = docs + [query]
    n_docs = len(corpus)
    df: Counter[str] = Counter()
    for d in corpus:
        for t in set(d):
            df[t] += 1
    avgdl = sum(len(d) for d in corpus) / n_docs if n_docs else 1.0

    def score(d: list[str]) -> float:
        tf = Counter(d)
        s = 0.0
        for t in set(query):
            if t not in tf:
                continue
            idf = math.log(1 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
        return s

    self_score = score(query)
    if self_score <= 0:
        return [0.0 for _ in docs]
    return [score(d) / self_score for d in docs]


def register(ctx: UserContext, brief: dict, *, visibility: str | None = None) -> tuple[dict, bool]:
    """Create or update the node for a brief. Returns (node, statement_changed).
    The caller decides what to do about `statement_changed` (kick the advisor)."""
    st = _onboarding.statement(brief)
    sha = _onboarding.statement_sha(st)
    vis = visibility or brief.get("visibility") or "org"
    if vis not in _onboarding.VISIBILITIES:
        vis = "org"
    node = load_node(brief.get("node_id") or "") if brief.get("node_id") else None
    if node is not None and not is_owner(node, ctx.user.user_id):
        node = None  # never adopt someone else's node
    if node is None:
        node = node_for_brief(ctx.user.user_id, str(brief.get("id")))
    inv = harness_inventory(ctx.root)
    ts = now_str()
    changed = True
    if node is None:
        node = {
            "id": new_node_id(),
            "owner": {"user_id": ctx.user.user_id, "display_name": ctx.user.display_name},
            "visibility": vis,
            "status": "registered",
            "statement": st,
            "statement_sha256": sha,
            "statement_revisions": [{"sha256": sha, "ts": ts}],
            "source": {"user_id": ctx.user.user_id, "brief_id": brief.get("id")},
            "parent_node": brief.get("parent_node") or None,
            "links": {"sessions": [], "harness_versions": [], "tools": [], "custom_tools": [], "roles": [], "datasets": [], "outcomes": []},
            "created": ts,
            "updated": ts,
        }
        kind = "node.registered"
    else:
        changed = node.get("statement_sha256") != sha
        node["statement"] = st
        if changed:
            node["statement_sha256"] = sha
            node.setdefault("statement_revisions", []).append({"sha256": sha, "ts": ts})
        node["visibility"] = vis
        node["parent_node"] = brief.get("parent_node") or None
        node["owner"]["display_name"] = ctx.user.display_name
        kind = "node.updated"
    links = node.setdefault("links", {})
    _refresh_harness_links(node, inv)
    links["datasets"] = dataset_fingerprints(ctx.library, brief.get("data") or [])
    # A parent link is a human-declared subproblem edge (child → parent). The
    # parent must be visible to the owner (never another tenant's private node).
    parent = node.get("parent_node")
    pn = load_node(parent) if parent and parent != node["id"] else None
    if pn and visible(pn, ctx.user.user_id):
        _write_edge(node["id"], parent, "subproblem", weight=1.0, source="human", status="confirmed", rationale="Started as a subproblem of the parent.")
    else:
        node["parent_node"] = None
    save_node(node)
    event(kind, node=node["id"], user=ctx.user.user_id, visibility=vis, changed=changed)
    recompute_edges(node)
    return node, changed


def set_status(node: dict, status: str) -> dict:
    if status not in NODE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(NODE_STATUSES)}")
    node["status"] = status
    save_node(node)
    event("node.status", node=node["id"], status=status)
    return node


def patch_node(node: dict, fields: dict) -> dict:
    if fields.get("status"):
        if fields["status"] not in NODE_STATUSES:
            raise ValueError(f"status must be one of {', '.join(NODE_STATUSES)}")
        node["status"] = fields["status"]
    if fields.get("visibility"):
        if fields["visibility"] not in _onboarding.VISIBILITIES:
            raise ValueError("visibility must be org or private")
        node["visibility"] = fields["visibility"]
    if fields.get("keywords") is not None:
        node.setdefault("statement", {})["keywords"] = _onboarding._clean_keywords(fields["keywords"])
        node["statement_sha256"] = _onboarding.statement_sha(node["statement"])
    save_node(node)
    event("node.updated", node=node["id"], patch=[k for k, v in fields.items() if v is not None])
    return node


# ---------- edges ----------


def _edge_id(src: str, dst: str, etype: str) -> str:
    return hashlib.sha1(f"{src}|{dst}|{etype}".encode("utf-8")).hexdigest()[:12]


def _canon(src: str, dst: str, etype: str) -> tuple[str, str]:
    if etype in SYMMETRIC_TYPES and dst < src:
        return dst, src
    return src, dst


def _write_edge(src: str, dst: str, etype: str, *, weight: float, source: str, status: str, rationale: str = "", rec_id: str | None = None) -> dict:
    if etype not in EDGE_TYPES:
        raise ValueError(f"edge type must be one of {', '.join(EDGE_TYPES)}")
    src, dst = _canon(src, dst, etype)
    rec = {
        "id": _edge_id(src, dst, etype),
        "src": src,
        "dst": dst,
        "type": etype,
        "weight": round(float(weight), 3),
        "source": source,
        "status": status,
        "rationale": (rationale or "")[:600],
        "rec_id": rec_id,
        "ts": now_str(),
    }
    append_jsonl(edges_path(), rec)
    return rec


def current_edges() -> dict[str, dict]:
    """The live edge set: last record per edge id."""
    out: dict[str, dict] = {}
    p = edges_path()
    if not p.exists():
        return out
    import json

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                out[rec["id"]] = rec
    return out


def edges_for(pid: str, *, include_dropped: bool = False) -> list[dict]:
    return [
        e
        for e in current_edges().values()
        if (e["src"] == pid or e["dst"] == pid) and (include_dropped or e.get("status") not in ("dropped", "rejected"))
    ]


def visible_edges(pid: str, uid: str) -> list[dict]:
    """Edges of ``pid`` whose OTHER endpoint the viewer may see. A private
    node must not leak through an edge's id or rationale."""
    out: list[dict] = []
    for e in edges_for(pid):
        other = load_node(e["dst"] if e["src"] == pid else e["src"])
        if other and visible(other, uid):
            out.append(e)
    return out


def recompute_edges(node: dict) -> list[dict]:
    """Deterministic adjacency: BM25 over statements (top-8, normalised ≥
    0.25, ≥3 shared informative terms), equal dataset fingerprints →
    shares_dataset, shared non-base tools → shares_method. Human/advisor edges
    are never overwritten; a keyword edge that no longer qualifies is dropped."""
    pid = node["id"]
    owner = (node.get("owner") or {}).get("user_id", "")
    others = [n for n in all_nodes() if n["id"] != pid and (n.get("visibility") == "org" or is_owner(n, owner))]
    live = current_edges()
    written: list[dict] = []

    def existing(other: str, etype: str) -> dict | None:
        s, d = _canon(pid, other, etype)
        return live.get(_edge_id(s, d, etype))

    def maybe_write(other: str, etype: str, weight: float, rationale: str) -> None:
        cur = existing(other, etype)
        if cur and cur.get("source") in ("human", "advisor", "import") and cur.get("status") != "dropped":
            return  # a typed, rationalised edge outranks the keyword pass
        if cur and cur.get("status") in ("confirmed", "rejected"):
            return  # a human decision is never overwritten by the keyword pass
        if cur and cur.get("status") == "proposed" and abs(cur.get("weight", 0) - weight) < 0.005:
            return  # unchanged
        written.append(_write_edge(pid, other, etype, weight=weight, source="keyword", status="proposed", rationale=rationale))

    # --- BM25 adjacency
    qualified: set[str] = set()
    if others:
        docs = [tokenize(_statement_text(n.get("statement") or {})) for n in others]
        query = tokenize(_statement_text(node.get("statement") or {}))
        if query and any(docs):
            scores = bm25_scores(query, docs)
            qset = set(query)
            ranked = sorted(range(len(others)), key=lambda i: -scores[i])
            for i in ranked[:ADJACENCY_TOP_K]:
                norm = scores[i]
                shared = sorted(qset & set(docs[i]))
                if norm < ADJACENCY_MIN_NORM or len(shared) < ADJACENCY_MIN_SHARED_TERMS:
                    continue
                qualified.add(others[i]["id"])
                maybe_write(others[i]["id"], "adjacent", min(1.0, norm), "Shared terms: " + ", ".join(shared[:8]))
    # --- shared datasets / methods
    my_fps = {d.get("fingerprint") for d in ((node.get("links") or {}).get("datasets") or []) if d.get("fingerprint")}
    my_tools = set((node.get("links") or {}).get("custom_tools") or [])
    ds_qualified: set[str] = set()
    tool_qualified: set[str] = set()
    for n in others:
        fps = {d.get("fingerprint") for d in ((n.get("links") or {}).get("datasets") or []) if d.get("fingerprint")}
        common = my_fps & fps
        if common:
            ds_qualified.add(n["id"])
            maybe_write(n["id"], "shares_dataset", 1.0, f"{len(common)} identical attached dataset(s).")
        tools = set((n.get("links") or {}).get("custom_tools") or [])
        common_t = my_tools & tools
        if common_t:
            tool_qualified.add(n["id"])
            maybe_write(n["id"], "shares_method", min(1.0, 0.5 + 0.25 * len(common_t)), "Shared evolved tools: " + ", ".join(sorted(common_t)[:6]))
    # --- drop keyword edges that no longer qualify
    for e in live.values():
        if e.get("source") != "keyword" or e.get("status") != "proposed":
            continue
        if pid not in (e["src"], e["dst"]):
            continue
        other = e["dst"] if e["src"] == pid else e["src"]
        ok = (e["type"] == "adjacent" and other in qualified) or (e["type"] == "shares_dataset" and other in ds_qualified) or (e["type"] == "shares_method" and other in tool_qualified)
        if not ok:
            written.append(_write_edge(e["src"], e["dst"], e["type"], weight=e.get("weight", 0), source="keyword", status="dropped", rationale=e.get("rationale", "")))
    for e in written:
        event("edge.proposed" if e["status"] == "proposed" else "edge.dropped", edge=e["id"], src=e["src"], dst=e["dst"], type=e["type"], weight=e["weight"])
    return written


def declare_edge(node: dict, dst: str, etype: str, rationale: str, uid: str) -> dict:
    if not load_node(dst):
        raise KeyError(dst)
    e = _write_edge(node["id"], dst, etype, weight=1.0, source="human", status="confirmed", rationale=rationale)
    event("edge.confirmed", edge=e["id"], src=e["src"], dst=e["dst"], type=e["type"], user=uid)
    return e


def decide_edge(eid: str, uid: str, decision: str) -> dict:
    """Confirm or reject an edge; allowed for the owner of either endpoint."""
    e = current_edges().get(eid)
    if not e:
        raise KeyError(eid)
    a, b = load_node(e["src"]), load_node(e["dst"])
    if not ((a and is_owner(a, uid)) or (b and is_owner(b, uid))):
        raise PermissionError(eid)
    status = "confirmed" if decision == "confirm" else "rejected"
    rec = _write_edge(e["src"], e["dst"], e["type"], weight=e.get("weight", 1.0), source=e.get("source", "human"), status=status, rationale=e.get("rationale", ""), rec_id=e.get("rec_id"))
    event("edge." + status, edge=rec["id"], src=rec["src"], dst=rec["dst"], type=rec["type"], user=uid)
    return rec


# ---------- views ----------


def _one_line(text: str, n: int = OUTCOME_MAX_CHARS) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def node_summary(node: dict) -> dict:
    st = node.get("statement") or {}
    links = node.get("links") or {}
    outcomes = links.get("outcomes") or []
    last = outcomes[-1] if outcomes else None
    active = next((v for v in (links.get("harness_versions") or []) if v.get("active")), None)
    return {
        "id": node["id"],
        "title": st.get("title", ""),
        "research_question": st.get("research_question", ""),
        "domain": st.get("domain", ""),
        "keywords": list(st.get("keywords") or []),
        "owner": dict(node.get("owner") or {}),
        "visibility": node.get("visibility", "org"),
        "status": node.get("status", "registered"),
        "parent_node": node.get("parent_node"),
        "created": node.get("created"),
        "updated": node.get("updated"),
        "session_count": len(links.get("sessions") or []),
        "outcome": _one_line(last.get("summary", "")) if last else "",
        "active_version": {"id": active.get("id"), "summary": active.get("summary"), "imported_from": active.get("imported_from")} if active else None,
        "version_count": len(links.get("harness_versions") or []),
        "custom_tools": list(links.get("custom_tools") or []),
        "dataset_count": len(links.get("datasets") or []),
    }


def tree(uid: str) -> dict:
    nodes = [n for n in all_nodes() if visible(n, uid)]
    ids = {n["id"] for n in nodes}
    edges = [e for e in current_edges().values() if e["src"] in ids and e["dst"] in ids and e.get("status") not in ("dropped", "rejected")]
    return {"nodes": [node_summary(n) for n in nodes], "edges": edges, "me": uid}


def neighbours(pid: str, uid: str, limit: int = ADJACENCY_TOP_K) -> list[dict]:
    out: list[dict] = []
    for e in edges_for(pid):
        other_id = e["dst"] if e["src"] == pid else e["src"]
        other = load_node(other_id)
        if not other or not visible(other, uid):
            continue
        out.append({"node": node_summary(other), "edge": e})
    rank = {"confirmed": 0, "proposed": 1}
    out.sort(key=lambda x: (rank.get(x["edge"].get("status"), 2), -float(x["edge"].get("weight") or 0)))
    return out[:limit]


def node_view(pid: str, uid: str) -> dict | None:
    node = load_node(pid)
    if not node or not visible(node, uid):
        return None
    links = node.get("links") or {}
    view = {
        **node_summary(node),
        "statement": node.get("statement") or {},
        "statement_sha256": node.get("statement_sha256"),
        "revisions": len(node.get("statement_revisions") or []),
        "links": {
            "sessions": list(links.get("sessions") or []),
            "harness_versions": list(links.get("harness_versions") or []),
            "tools": list(links.get("tools") or []),
            "custom_tools": list(links.get("custom_tools") or []),
            "roles": list(links.get("roles") or []),
            "datasets": [{"path": d.get("path"), "size": d.get("size"), "kind": d.get("kind")} for d in (links.get("datasets") or [])],
            "outcomes": [
                {"session_id": o.get("session_id"), "summary": _one_line(o.get("summary", ""), 600), "results": list(o.get("results") or [])[:20], "version": o.get("version"), "ts": o.get("ts")}
                for o in (links.get("outcomes") or [])
            ],
        },
        "edges": visible_edges(pid, uid),
        "neighbours": neighbours(pid, uid),
        "is_owner": is_owner(node, uid),
    }
    if is_owner(node, uid):
        view["source"] = node.get("source")
        view["recommendation"] = latest_recommendation_summary(pid)
        # The owner's full version inventory stays owner-only; colleagues see
        # only the versions this problem actually ran on (links.harness_versions).
        view["harness_inventory"] = list(node.get("harness_inventory") or [])
    return view


def near(pid: str, uid: str) -> dict | None:
    node = load_node(pid)
    if not node or not visible(node, uid):
        return None
    return {"self": node_summary(node), "neighbours": neighbours(pid, uid), "edges": visible_edges(pid, uid)}


def latest_recommendation_summary(pid: str) -> dict | None:
    rec = read_json(recs_dir(pid) / "latest.json", None)
    if not isinstance(rec, dict):
        return None
    return {
        "rec_id": rec.get("rec_id"),
        "status": rec.get("status"),
        "served_by": rec.get("served_by"),
        "created": rec.get("created"),
        "harness_import": bool((rec.get("harness_import") or {}).get("recommended")),
        "tool_imports": len(rec.get("tool_imports") or []),
        "related": len(rec.get("related_problems") or []),
    }


def render_tree_context(pid: str | None, uid: str) -> str:
    """The server-composed 'Project tree context' section of the brief."""
    if not pid:
        return ""
    node = load_node(pid)
    if not node:
        return ""
    lines = [f"**Registered as project node** `{pid}` ({node.get('visibility', 'org')}, status {node.get('status', 'registered')})."]
    parent = node.get("parent_node")
    if parent:
        pn = load_node(parent)
        if pn and visible(pn, uid):
            lines.append(f"**Derives from:** {(pn.get('statement') or {}).get('title', parent)} (`{parent}`).")
    nbrs = neighbours(pid, uid, limit=5)
    if nbrs:
        lines.append("**Adjacent problems** (reuse their findings and methods; do not repeat them):")
        for x in nbrs:
            n = x["node"]
            e = x["edge"]
            who = (n.get("owner") or {}).get("display_name") or "?"
            bits = [f"- {n['title']} — {who}; {e['type']} ({e.get('status')}, w={e.get('weight')})"]
            if n.get("outcome"):
                bits.append(f"  outcome: {n['outcome']}")
            if n.get("active_version"):
                bits.append(f"  harness: {n['active_version'].get('summary') or n['active_version'].get('id')}")
            if n.get("custom_tools"):
                bits.append("  evolved tools: " + ", ".join(n["custom_tools"][:6]))
            lines.extend(bits)
    else:
        lines.append("_No adjacent problems on the tree yet._")
    return "\n".join(lines)


# ---------- session links / outcome sync ----------


def link_session(node: dict, sid: str, harness: dict | None, root: Path | None = None) -> dict:
    """At launch: attach the session and refresh the harness links from the
    tenant root, so a version evolved after registration becomes visible (and
    discoverable to colleagues) as soon as a shared problem runs on it."""
    links = node.setdefault("links", {})
    sessions = links.setdefault("sessions", [])
    if sid not in sessions:
        sessions.append(sid)
    if harness:
        node["last_harness"] = harness
    if root is not None:
        try:
            _refresh_harness_links(node, harness_inventory(root))
        except Exception:
            pass
    if node.get("status") == "registered":
        node["status"] = "active"
    save_node(node)
    event("node.status", node=node["id"], status=node["status"], session=sid)
    return node


def sync_session(ctx: UserContext, sid: str) -> dict | None:
    """After a research turn completes: record the outcome one-liner, the
    results files and the version it ran on, on the session's node."""
    brief = read_json(ctx.session_dir(sid) / _onboarding.BRIEF_FILENAME, None)
    pid = brief.get("node_id") if isinstance(brief, dict) else None
    node = load_node(pid) if pid else node_for_session(ctx.user.user_id, sid)
    if not node or not is_owner(node, ctx.user.user_id):
        return None
    import json

    summary = ""
    ev_path = ctx.session_dir(sid) / "events.jsonl"
    if ev_path.exists():
        try:
            with open(ev_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    k = e.get("kind")
                    if k == "research.complete":
                        summary = str(e.get("summary") or e.get("reason") or summary)
                    elif k == "bus.send" and e.get("target") == "human":
                        payload = e.get("payload")
                        t = payload.get("text") if isinstance(payload, dict) else None
                        if t:
                            summary = str(t)
        except OSError:
            pass
    results: list[str] = []
    rd = ctx.session_dir(sid) / "results"
    if rd.is_dir():
        for p in sorted(rd.rglob("*")):
            if p.is_file():
                results.append(p.relative_to(rd).as_posix())
            if len(results) >= 50:
                break
    try:
        active = archive.list_versions(ctx.root).get("active")
    except Exception:
        active = None
    outcome = {"session_id": sid, "summary": _one_line(summary, 1200), "results": results, "version": active, "ts": now_str()}
    links = node.setdefault("links", {})
    outs = [o for o in (links.get("outcomes") or []) if o.get("session_id") != sid]
    outs.append(outcome)
    links["outcomes"] = outs
    if sid not in (links.get("sessions") or []):
        links.setdefault("sessions", []).append(sid)
    _refresh_harness_links(node, harness_inventory(ctx.root))
    save_node(node)
    event("node.updated", node=node["id"], session=sid, outcome=bool(summary))
    recompute_edges(node)
    return outcome


# ---------- spawn a subproblem brief ----------


def spawn_brief_fields(parent: dict) -> dict:
    """Prefill for a brief started from a node: same domain and keywords, the
    parent's statement as prior work, and the parent link."""
    st = parent.get("statement") or {}
    prior = f"Derived from \"{st.get('title', '')}\" ({parent['id']}). That problem asks: {st.get('research_question', '')}"
    if st.get("open_gap"):
        prior += f"\n\nWhat it left open: {st['open_gap']}"
    outs = (parent.get("links") or {}).get("outcomes") or []
    if outs:
        prior += f"\n\nIts latest outcome: {_one_line(outs[-1].get('summary', ''), 600)}"
    return {
        "domain": st.get("domain", ""),
        "keywords": list(st.get("keywords") or []),
        "prior_work": prior,
        "parent_node": parent["id"],
        "visibility": parent.get("visibility", "org"),
    }


# ---------- cross-tenant catalogue (read-only, bounded) ----------


def _display_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for n in all_nodes():
        o = n.get("owner") or {}
        if o.get("user_id") and o.get("display_name"):
            names[o["user_id"]] = o["display_name"]
    try:
        from . import auth

        for u in auth.list_users(include_disabled=True):
            names.setdefault(u.user_id, u.display_name)
    except Exception:
        pass
    return names


def _git(*args: str, repo: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=str(repo), stderr=subprocess.STDOUT, text=True, timeout=30)


def tools_at(repo: Path, sha: str) -> list[str]:
    """Tool package names present in a tenant repo at a commit (git ls-tree)."""
    try:
        raw = _git("ls-tree", "--name-only", sha, "tools/", repo=repo)
    except Exception:
        return []
    out = []
    for line in raw.splitlines():
        name = line.strip().removeprefix("tools/").strip("/")
        if name and not name.startswith(("_", ".")) and "." not in name:
            out.append(name)
    return sorted(out)


def roles_at(repo: Path, sha: str) -> list[str]:
    try:
        raw = _git("ls-tree", "--name-only", sha, "roles/subagents/", repo=repo)
    except Exception:
        return []
    out = ["supervisor"]
    for line in raw.splitlines():
        name = Path(line.strip()).stem
        if line.strip().endswith(".yaml") and name:
            out.append(name)
    return out


def tool_description_at(repo: Path, sha: str, name: str) -> str:
    """First docstring line of tools/<name>/server.py at a commit (bounded)."""
    try:
        src = _git("show", f"{sha}:tools/{name}/server.py", repo=repo)
    except Exception:
        return ""
    m = re.search(r'"""(.*?)"""', src, re.DOTALL)
    if not m:
        return ""
    return _one_line(m.group(1).strip().split("\n\n")[0], 240)


def version_discoverable(owner_uid: str, vid: str) -> bool:
    """A version is importable only if linked to a non-private launched node,
    or explicitly shared by its owner (POST /versions/{id}/share)."""
    for n in all_nodes():
        if not is_owner(n, owner_uid) or n.get("visibility") != "org":
            continue
        links = n.get("links") or {}
        if not links.get("sessions"):
            continue
        if any(v.get("id") == vid for v in links.get("harness_versions") or []):
            return True
    meta = read_json(user_root(owner_uid) / "state" / "archive" / "evolutions" / vid / "meta.json", None)
    return bool(isinstance(meta, dict) and meta.get("shared"))


def importable_catalogue(uid: str, *, max_versions: int = 20, max_tools: int = 30) -> dict:
    """Every version another tenant has made discoverable, with the non-base
    tools it carries. Read-only; bounded. Excludes the caller's own tenant."""
    base = base_tool_names()
    names = _display_names()
    versions: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_version(owner: str, vid: str, meta: dict | None, from_node: str | None, node_title: str | None) -> None:
        key = (owner, vid)
        if key in seen or len(versions) >= max_versions:
            return
        repo = user_root(owner)
        meta = meta if isinstance(meta, dict) else (read_json(repo / "state" / "archive" / "evolutions" / vid / "meta.json", None) or {})
        sha = meta.get("sha")
        if not sha:
            try:
                sha = _git("rev-list", "-n", "1", "ver/" + vid, repo=repo).strip()
            except Exception:
                return
        seen.add(key)
        tool_names = tools_at(repo, sha)
        versions.append(
            {
                "owner": owner,
                "owner_name": names.get(owner, owner),
                "version_id": vid,
                "sha": sha,
                "summary": _one_line(meta.get("summary", ""), 200),
                "rationale": _one_line(meta.get("rationale", ""), 600),
                "created_at": meta.get("created_at"),
                "from_node": from_node,
                "from_node_title": node_title,
                "tools": tool_names,
                "custom_tools": [t for t in tool_names if t not in base],
                "roles": roles_at(repo, sha),
                "imported_from": meta.get("imported_from"),
                "smoke": meta.get("smoke"),
            }
        )

    # (1) versions linked to shared, launched problems
    for n in all_nodes():
        owner = (n.get("owner") or {}).get("user_id")
        if not owner or owner == uid or n.get("visibility") != "org":
            continue
        links = n.get("links") or {}
        if not links.get("sessions"):
            continue
        for v in links.get("harness_versions") or []:
            if v.get("id"):
                add_version(owner, v["id"], None, n["id"], (n.get("statement") or {}).get("title"))
    # (2) explicitly shared versions
    if USERS_DIR.is_dir():
        for udir in sorted(USERS_DIR.iterdir()):
            owner = udir.name
            if owner == uid:
                continue
            arch = udir / "root" / "state" / "archive" / "evolutions"
            if not arch.is_dir():
                continue
            for meta_path in sorted(arch.glob("*/meta.json")):
                meta = read_json(meta_path, None)
                if isinstance(meta, dict) and meta.get("shared") and meta.get("id"):
                    add_version(owner, meta["id"], meta, None, None)
    # tool catalogue: unique (owner, tool) from the discoverable versions
    tools: list[dict] = []
    seen_t: set[tuple[str, str]] = set()
    for v in versions:
        for t in v["custom_tools"]:
            key = (v["owner"], t)
            if key in seen_t or len(tools) >= max_tools:
                continue
            seen_t.add(key)
            tools.append(
                {
                    "name": t,
                    "owner": v["owner"],
                    "owner_name": v["owner_name"],
                    "version_id": v["version_id"],
                    "sha": v["sha"],
                    "description": tool_description_at(user_root(v["owner"]), v["sha"], t),
                    "from_node": v.get("from_node"),
                }
            )
    return {"versions": versions, "tools": tools}
