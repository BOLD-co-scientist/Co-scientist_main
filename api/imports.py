"""Cross-tenant harness and tool imports (O2), built on R17's version nodes.

Transport is git: every tenant root is a git repo on the same filesystem, so
the importer fetches the donor's ``ver/<vid>`` tag into
``refs/imports/<owner>/<vid>`` of its own repo (``git fetch --no-tags <path>``)
and everything after that is local. The donor repo is only ever READ.

Two harness modes (the advisor gives a hint; the human chooses):
- adopt  — tag the fetched commit as a new version of the importer's repo
           (``record_imported_version``: meta.json + diff.patch + rollback
           target) and switch to it with the R17 mechanics (detached checkout
           under the switch lock, busy-guarded). ``state/`` is untouched;
           rollback = activate the previous version.
- merge  — a worktree off the importer's HEAD, ``git apply --3way`` of the
           donor's whole evolution delta, smoke + compat gate, fast-forward.
           On apply conflicts the import is handed to the evolution agent.

Tool import is deterministic and API-side: subset checkout of
``tools/<name>/`` from the fetched commit into a worktree off HEAD, a textual
insert into the chosen roles' ``tools:`` lists, commit, smoke + compat gate,
then a fast-forward and a child version. Only when the smoke fails does the
API compose an evolution command (the tool needs scaffold deltas from the
donor) and route it through the normal evolution runtime and merge gate.

Every import is HUMAN-GATED: the API opens a pending HITL request in a
dedicated ``evo-import-<iid>`` session using the same file protocol as
scaffold/hitl.py (kind ``harness_import`` | ``tool_import``), so it shows up in
the existing approval panel and is answered through ``POST
/hitl/{sid}/{rid}/answer``. A pre-gate smoke runs BEFORE the human is asked
(the R17 rule). No runtime owns that session, so autonomous mode can never
answer it. Ledger: ``state/projects/imports/<iid>.json``.
"""

from __future__ import annotations

import os
import re
import resource
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scaffold import archive, settings
from scaffold._atomic import append_jsonl, read_json, write_json

from . import projects
from .tenancy import UserContext, user_root


SMOKE_TESTS = ("tests/test_smoke_v0.py", "tests/test_contract_compat.py")
SMOKE_TIMEOUT_S = int(os.environ.get("COSCIENTIST_IMPORT_SMOKE_TIMEOUT_S", "300"))
SMOKE_MEM_BYTES = int(os.environ.get("COSCIENTIST_IMPORT_SMOKE_MEM_MB", "4096")) * 1024 * 1024
DIFF_PREVIEW_CHARS = 8000
HARNESS_DIRS = ("scaffold", "research", "evolution", "tools", "roles", "prompts")


class ImportError_(RuntimeError):
    """A user-facing import failure (the HTTP layer maps it to 4xx)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_import_id() -> str:
    return "imp-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def session_id_for(iid: str) -> str:
    return f"evo-import-{iid}"


def import_id_from_session(sid: str) -> str | None:
    return sid[len("evo-import-") :] if sid.startswith("evo-import-") else None


def _slug(text: str, n: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n] or "import"


# ---------- ledger ----------


def ledger_path(iid: str) -> Path:
    return projects.imports_dir() / f"{iid}.json"


def load_import(iid: str) -> dict | None:
    if not re.fullmatch(r"imp-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", iid or ""):
        return None
    try:
        rec = read_json(ledger_path(iid), None)
    except Exception:
        return None
    return rec if isinstance(rec, dict) and rec.get("id") == iid else None


def save_import(rec: dict) -> dict:
    rec["updated"] = now_str()
    write_json(ledger_path(rec["id"]), rec)
    return rec


def list_imports(uid: str) -> list[dict]:
    out = []
    for p in projects.imports_dir().glob("imp-*.json"):
        try:
            rec = read_json(p, None)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("user_id") == uid:
            out.append(rec)
    out.sort(key=lambda r: r.get("created") or "", reverse=True)
    return out


PREPARING_MAX_S = 30 * 60


def pending_imports(uid: str, ctx: UserContext | None = None) -> list[dict]:
    """Imports that block a new one: awaiting approval, or still being prepared.
    With ``ctx`` the ledger self-heals: a `pending` record whose HITL request
    file is gone (session deleted, crash between answer and apply) and a
    `preparing` record older than PREPARING_MAX_S are marked failed."""
    out: list[dict] = []
    for r in list_imports(uid):
        st = r.get("status")
        if st not in ("pending", "preparing"):
            continue
        if ctx is not None:
            if st == "pending" and r.get("request_id") and not (ctx.session_dir(r["session_id"]) / "hitl" / "pending" / f"{r['request_id']}.json").exists():
                r.update({"status": "failed", "error": "the approval request no longer exists (session deleted or interrupted); request the import again"})
                save_import(r)
                _remove_worktree(ctx.root, r["id"])
                continue
            if st == "preparing":
                try:
                    age = time.time() - time.mktime(time.strptime(r.get("created", ""), "%Y-%m-%d %H:%M:%S"))
                except (ValueError, OverflowError):
                    age = 0
                if age > PREPARING_MAX_S:
                    r.update({"status": "failed", "error": "preparation never finished (API restarted?); request the import again"})
                    save_import(r)
                    _remove_worktree(ctx.root, r["id"])
                    continue
        out.append(r)
    return out


# ---------- git helpers ----------


def _git(*args: str, repo: Path, timeout: int = 120) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=str(repo), stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        raise ImportError_(500, f"git {' '.join(args[:2])} failed: {e.output[-800:]}")


def _head(repo: Path) -> str:
    return _git("rev-parse", "HEAD", repo=repo).strip()


def _ref_name(owner: str, vid: str) -> str:
    return f"refs/imports/{owner}/{vid}"


def donor_meta(owner: str, vid: str) -> dict | None:
    meta = read_json(user_root(owner) / "state" / "archive" / "evolutions" / vid / "meta.json", None)
    return meta if isinstance(meta, dict) else None


def fetch_version(importer_root: Path, owner: str, vid: str) -> str:
    """Fetch the donor's ``ver/<vid>`` tag into ``refs/imports/<owner>/<vid>``
    and return the commit sha, checked against the donor's manifest."""
    donor_root = user_root(owner)
    if not (donor_root / ".git").exists():
        raise ImportError_(404, "the donor tenant has no harness repository")
    if not re.fullmatch(r"[0-9A-Za-z._][0-9A-Za-z._-]{0,119}", vid or ""):
        raise ImportError_(400, f"invalid version id: {vid!r}")
    try:
        expected = subprocess.check_output(["git", "rev-list", "-n", "1", "ver/" + vid], cwd=str(donor_root), stderr=subprocess.STDOUT, text=True, timeout=30).strip()
    except subprocess.CalledProcessError:
        raise ImportError_(404, f"the donor has no version {vid!r}")
    _git("fetch", "--no-tags", "--quiet", str(donor_root), f"+refs/tags/ver/{vid}:{_ref_name(owner, vid)}", repo=importer_root, timeout=300)
    got = _git("rev-list", "-n", "1", _ref_name(owner, vid), repo=importer_root).strip()
    if got != expected:
        raise ImportError_(500, "fetched commit does not match the donor's tag")
    meta = donor_meta(owner, vid)
    if meta and meta.get("sha") and meta["sha"] != got:
        raise ImportError_(409, "the donor's manifest disagrees with its git tag; refusing to import")
    return got


def drop_ref(importer_root: Path, owner: str, vid: str) -> None:
    try:
        subprocess.run(["git", "update-ref", "-d", _ref_name(owner, vid)], cwd=str(importer_root), capture_output=True, timeout=30)
    except Exception:
        pass


def _root_commit(repo: Path, sha: str) -> str:
    out = _git("rev-list", "--max-parents=0", sha, repo=repo).strip().splitlines()
    return out[-1] if out else sha


def donor_delta(importer_root: Path, sha: str) -> str:
    """Everything the donor evolved: the diff from the donor's bootstrap commit
    (its root) to the version, restricted to the harness dirs."""
    root = _root_commit(importer_root, sha)
    if root == sha:
        return ""
    return _git("diff", "--binary", root, sha, "--", *HARNESS_DIRS, repo=importer_root, timeout=120)


def _role_tools_at(repo: Path, sha: str) -> dict[str, list[str]]:
    """{role: [tools]} as declared in the role YAML files at a commit."""
    import yaml

    out: dict[str, list[str]] = {}
    for role in projects.roles_at(repo, sha):
        rel = "roles/supervisor.yaml" if role == "supervisor" else f"roles/subagents/{role}.yaml"
        try:
            raw = yaml.safe_load(_git("show", f"{sha}:{rel}", repo=repo)) or {}
        except Exception:
            continue
        out[role] = [str(t) for t in (raw.get("tools") or [])]
    return out


def merge_donor_into_worktree(wt: Path, repo: Path, sha: str) -> dict:
    """Merge a donor version into a worktree of the importer WITHOUT the
    conflicts a raw patch would produce for the common case. Decomposed:
      (a) tool packages the donor has and we lack → subset checkout (new files
          never conflict);
      (b) role wiring: every tool a donor role lists that our same role lacks
          → the textual insert used by tool imports (idempotent, no hunks);
          role files the donor has and we lack → checkout;
      (c) everything else the donor evolved (scaffold/research/evolution/
          prompts, edits to shared tools) → `git apply --3way` of the delta
          from the donor's bootstrap root, excluding what (a)/(b) handled.
    Returns a report; `conflict` is the git output when (c) could not apply."""
    report: dict[str, Any] = {"tools_added": [], "roles_added": [], "roles_wired": {}, "patched": False, "conflict": None}
    mine_tools = set(projects._tool_names_in(wt))
    donor_tools = projects.tools_at(repo, sha)
    excludes: list[str] = []
    for t in donor_tools:
        if t in mine_tools:
            continue
        _git("checkout", sha, "--", f"tools/{t}", repo=wt)
        report["tools_added"].append(t)
        excludes.append(f":(exclude)tools/{t}")
    mine_roles = set(projects._role_names_in(wt))
    donor_role_tools = _role_tools_at(repo, sha)
    for role, tools in donor_role_tools.items():
        rel = "roles/supervisor.yaml" if role == "supervisor" else f"roles/subagents/{role}.yaml"
        if role not in mine_roles:
            _git("checkout", sha, "--", rel, repo=wt)
            report["roles_added"].append(role)
            excludes.append(f":(exclude){rel}")
            continue
        path = wt / rel
        text = path.read_text(encoding="utf-8")
        added: list[str] = []
        for t in tools:
            # Every tool the donor's role lists and ours lacks — packages,
            # scaffold-provided servers (bus/memory/hitl), or the SDK's Task.
            # build_tools tolerates unknown names (logs tool.missing).
            text, changed = add_tool_to_role_yaml(text, t)
            if changed:
                added.append(t)
        if added:
            path.write_text(text, encoding="utf-8")
            report["roles_wired"][role] = added
        excludes.append(f":(exclude){rel}")
    root = _root_commit(repo, sha)
    patch = ""
    if root != sha:
        patch = _git("diff", "--binary", root, sha, "--", *HARNESS_DIRS, *excludes, repo=repo, timeout=120)
    if patch.strip():
        patch_path = wt / ".import.patch"
        patch_path.write_text(patch, encoding="utf-8")
        try:
            subprocess.run(["git", "apply", "--3way", "--index", str(patch_path)], cwd=str(wt), check=True, capture_output=True, text=True, timeout=120)
            report["patched"] = True
        except subprocess.CalledProcessError as e:
            report["conflict"] = (e.stdout + e.stderr)[-1500:]
        finally:
            patch_path.unlink(missing_ok=True)
    return report


def diffstat(repo: Path, a: str, b: str, *paths: str) -> str:
    try:
        return _git("diff", "--stat=100", a, b, "--", *(paths or HARNESS_DIRS), repo=repo, timeout=60)[-4000:]
    except ImportError_:
        return ""


def diff_preview(repo: Path, a: str, b: str, *paths: str) -> str:
    try:
        return _git("diff", a, b, "--", *(paths or HARNESS_DIRS), repo=repo, timeout=60)[:DIFF_PREVIEW_CHARS]
    except ImportError_:
        return ""


def risks_for(importer_root: Path, donor_root: Path, sha: str) -> list[str]:
    """Deterministic: what the importer's current tree has that the donor tree
    at ``sha`` lacks (tools, roles), and whether the donor carries the compat gate."""
    risks: list[str] = []
    if not sha:
        return ["donor version sha unknown; could not compare tool and role inventories"]
    donor_tools_probe = projects.tools_at(donor_root, sha)
    if not donor_tools_probe:
        return [f"could not read the donor tree at {sha[:8]}; tool and role inventories not compared"]
    mine_tools = set(projects._tool_names_in(importer_root))
    mine_roles = set(projects._role_names_in(importer_root))
    donor_tools = set(donor_tools_probe)
    donor_roles = set(projects.roles_at(donor_root, sha))
    for t in sorted(mine_tools - donor_tools):
        risks.append(f"tool `{t}` is in your harness but absent from the donor version")
    for r in sorted(mine_roles - donor_roles):
        risks.append(f"role `{r}` is in your harness but absent from the donor version")
    try:
        subprocess.check_output(["git", "cat-file", "-e", f"{sha}:tests/test_contract_compat.py"], cwd=str(donor_root), stderr=subprocess.STDOUT, timeout=30)
    except Exception:
        risks.append("the donor version predates the R17 compat gate (no tests/test_contract_compat.py); only the v0 smoke can be run")
    return risks


def donor_skills(owner: str) -> list[str]:
    d = user_root(owner) / ".claude" / "skills"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


# ---------- worktrees + smoke ----------


def _wt_path(root: Path, iid: str) -> Path:
    return root / "worktrees" / f"import-{iid}"


def _branch(iid: str) -> str:
    return f"import/{iid}"


def prune_stale_worktrees(root: Path) -> int:
    """Remove ``worktrees/import-*`` left behind by a crashed API (their ledger
    is no longer pending). Called before every new import."""
    n = 0
    wdir = root / "worktrees"
    if not wdir.is_dir():
        return 0
    for p in wdir.glob("import-*"):
        iid = p.name[len("import-") :]
        rec = load_import(iid)
        if rec and rec.get("status") in ("pending", "preparing"):
            continue  # another request's in-flight smoke or an approval still open
        _remove_worktree(root, iid)
        n += 1
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=str(root), capture_output=True, timeout=30)
    except Exception:
        pass
    return n


def _remove_worktree(root: Path, iid: str) -> None:
    p = _wt_path(root, iid)
    try:
        subprocess.run(["git", "worktree", "remove", "--force", str(p)], cwd=str(root), capture_output=True, timeout=60)
    except Exception:
        pass
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    try:
        subprocess.run(["git", "branch", "-D", _branch(iid)], cwd=str(root), capture_output=True, timeout=30)
    except Exception:
        pass


def _add_worktree(root: Path, iid: str, *, detach_at: str | None = None) -> Path:
    path = _wt_path(root, iid)
    (root / "worktrees").mkdir(parents=True, exist_ok=True)
    if path.exists():
        _remove_worktree(root, iid)
    if detach_at:
        _git("worktree", "add", "--detach", str(path), detach_at, repo=root)
    else:
        _git("worktree", "add", "-b", _branch(iid), str(path), "HEAD", repo=root)
    return path


def run_smoke(worktree: Path) -> dict:
    """The smoke + compat gate, in the worktree, as the platform runs it for
    evolution merges (propose_merge._run_smoke). The env points COSCIENTIST_ROOT
    and PYTHONPATH at the worktree so the tests exercise THAT tree."""
    tests = [t for t in SMOKE_TESTS if (worktree / t).exists()]
    if not tests:
        return {"ran": False, "ok": None, "log": "no smoke tests in the tree", "tests": []}
    # The tree under test is a COLLEAGUE's code, run before any approval: give
    # it a minimal environment — no API keys or other secrets inherited from
    # the API process (only PATH/HOME/locale; HOME stays real because the
    # Python deps live in the user site-packages) — a throwaway TMPDIR, and
    # hard resource limits, the posture the per-tool sandboxes take.
    scratch = worktree / ".smoke-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(scratch)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
        "TMPDIR": str(scratch),
        "COSCIENTIST_ROOT": str(worktree),
        "PYTHONPATH": str(worktree),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for k in ("PYTHONUSERBASE", "VIRTUAL_ENV", "CONDA_PREFIX"):
        if os.environ.get(k):
            env[k] = os.environ[k]

    def _limits() -> None:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (SMOKE_TIMEOUT_S, SMOKE_TIMEOUT_S))
            resource.setrlimit(resource.RLIMIT_AS, (SMOKE_MEM_BYTES, SMOKE_MEM_BYTES))
            resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
        except (ValueError, OSError):
            pass

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            cwd=str(worktree),
            env=env,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_S,
            preexec_fn=_limits,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(scratch, ignore_errors=True)
        return {"ran": True, "ok": False, "log": "smoke timeout", "tests": tests}
    shutil.rmtree(scratch, ignore_errors=True)
    log = (proc.stdout + proc.stderr)[-4000:]
    return {"ran": True, "ok": proc.returncode == 0, "log": log, "tests": tests}


# ---------- HITL request (file protocol, API-side) ----------


def _session_dir(ctx: UserContext, sid: str) -> Path:
    sd = ctx.session_dir(sid)
    for sub in ("inbox", "status", "control", "hitl/pending", "hitl/answered", "memory/agent", "memory/project", "results", "scratch"):
        (sd / sub).mkdir(parents=True, exist_ok=True)
    return sd


def session_event(ctx: UserContext, sid: str, actor: str, kind: str, **fields: Any) -> str:
    eid = uuid.uuid4().hex[:12]
    append_jsonl(ctx.session_dir(sid) / "events.jsonl", {"id": eid, "ts": now_str(), "session": sid, "actor": actor, "kind": kind, **fields})
    return eid


def open_request(ctx: UserContext, sid: str, kind: str, summary: str, payload: dict) -> str:
    """Write a pending HITL request the way scaffold/hitl.py does, so the
    existing approval panel and answer route serve it unchanged."""
    _session_dir(ctx, sid)
    rid = uuid.uuid4().hex[:12]
    rec = {"id": rid, "ts": now_str(), "kind": kind, "summary": summary, "payload": payload, "decision": None}
    write_json(ctx.session_dir(sid) / "hitl" / "pending" / f"{rid}.json", rec)
    session_event(ctx, sid, actor="system", kind="hitl.pending", ref=rid, summary=summary)
    return rid


# ---------- role YAML wiring (textual, keeps the file's formatting) ----------


def add_tool_to_role_yaml(text: str, tool: str) -> tuple[str, bool]:
    """Insert ``- <tool>`` into the ``tools:`` list of a role YAML. Returns
    (new_text, changed). Idempotent; preserves everything else byte-for-byte."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        # Flow style: `tools: [a, b]` → insert inside the brackets.
        m_flow = re.match(r"^(tools:\s*\[)(.*?)(\]\s*(#.*)?\n?)$", line)
        if m_flow:
            items = [x.strip().strip("'\"") for x in m_flow.group(2).split(",") if x.strip()]
            if tool in items:
                return text, False
            items.append(tool)
            lines[i] = f"{m_flow.group(1)}{', '.join(items)}{m_flow.group(3)}"
            return "".join(lines), True
        if re.match(r"^tools:\s*(#.*)?$", line):
            j = i + 1
            indent = "  "
            last = i
            while j < len(lines):
                m = re.match(r"^(\s*)-\s+(.*?)\s*$", lines[j])
                if m:
                    indent = m.group(1) or "  "
                    if m.group(2).strip().strip("'\"") == tool:
                        return text, False
                    last = j
                    j += 1
                    continue
                if lines[j].strip() == "" or lines[j].lstrip().startswith("#"):
                    j += 1
                    continue
                break
            nl = "\n"
            lines.insert(last + 1, f"{indent}- {tool}{nl}")
            return "".join(lines), True
    sep = "" if text.endswith("\n") or not text else "\n"
    return text + f"{sep}tools:\n  - {tool}\n", True


def _role_yaml_path(root: Path, role: str) -> Path | None:
    if role == "supervisor":
        p = root / "roles" / "supervisor.yaml"
    else:
        p = root / "roles" / "subagents" / f"{role}.yaml"
    return p if p.exists() else None


# ---------- harness import ----------


def prepare_harness_import(
    ctx: UserContext,
    *,
    owner: str,
    version_id: str,
    mode: str,
    include_skills: list[str] | None,
    node_id: str | None,
    rec_id: str | None,
) -> dict:
    """Fetch, pre-gate smoke, open the HITL request. Returns the ledger record
    (status pending; or conflict → the caller may hand it to the evolution agent)."""
    uid = ctx.user.user_id
    if owner == uid:
        raise ImportError_(400, "that is your own version; switch to it from the Evolution tab instead")
    if mode not in ("adopt", "merge"):
        raise ImportError_(400, "mode must be adopt or merge")
    if not projects.version_discoverable(owner, version_id):
        raise ImportError_(403, "that version is not shared: it is not linked to a shared, launched problem and its owner has not shared it")
    if pending_imports(uid, ctx):
        raise ImportError_(409, "an import is already awaiting your approval; decide it first")
    iid = new_import_id()
    sid = session_id_for(iid)
    names = projects._display_names()
    meta = donor_meta(owner, version_id) or {}
    # The ledger record exists from the first moment (status `preparing`), so
    # a second request cannot slip in during the fetch + smoke and prune the
    # worktree this one is testing in.
    rec: dict[str, Any] = {
        "id": iid,
        "kind": "harness",
        "mode": mode,
        "user_id": uid,
        "owner": owner,
        "owner_name": names.get(owner, owner),
        "version_id": version_id,
        "sha": None,
        "node_id": node_id,
        "rec_id": rec_id,
        "session_id": sid,
        "request_id": None,
        "status": "preparing",
        "created": now_str(),
    }
    save_import(rec)
    try:
        return _prepare_harness_import(ctx, rec, meta=meta, include_skills=include_skills)
    except Exception as e:
        if rec.get("status") == "preparing":
            rec.update({"status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:600]}"})
            save_import(rec)
            _remove_worktree(ctx.root, iid)
            drop_ref(ctx.root, owner, version_id)
        raise


def _prepare_harness_import(ctx: UserContext, rec: dict, *, meta: dict, include_skills: list[str] | None) -> dict:
    uid = ctx.user.user_id
    iid, sid, owner, version_id, mode, node_id, rec_id = rec["id"], rec["session_id"], rec["owner"], rec["version_id"], rec["mode"], rec.get("node_id"), rec.get("rec_id")
    prune_stale_worktrees(ctx.root)
    sha = fetch_version(ctx.root, owner, version_id)
    head = _head(ctx.root)
    risks = risks_for(ctx.root, user_root(owner), sha)
    skills = donor_skills(owner)
    include = [s for s in (include_skills or []) if s in skills]
    for s in include:
        if (ctx.root / ".claude" / "skills" / s).exists():
            raise ImportError_(409, f"you already have a skill named {s!r}; deselect it or rename yours first")
    rec.update({
        "sha": sha,
        "summary": projects._one_line(meta.get("summary", ""), 200) or version_id,
        "rationale": projects._one_line(meta.get("rationale", ""), 600),
        "from_node": None,
        "status": "pending",
        "risks": risks,
        "donor_skills": skills,
        "include_skills": include,
        "rollback_to": {"sha": head, "version_id": archive.list_versions(ctx.root).get("active")},
    })
    for n in projects.all_nodes():
        if projects.is_owner(n, owner) and any(v.get("id") == version_id for v in ((n.get("links") or {}).get("harness_versions") or [])):
            rec["from_node"] = n["id"]
            rec["from_node_title"] = (n.get("statement") or {}).get("title")
            break
    # ---- pre-gate smoke, before the human is asked
    _session_dir(ctx, sid)
    session_event(ctx, sid, actor="human", kind="import.requested", import_id=iid, kind_detail="harness", mode=mode, owner=owner, version_id=version_id, sha=sha)
    projects.event("import.requested", import_id=iid, import_kind="harness", mode=mode, user=uid, owner=owner, version_id=version_id)
    try:
        if mode == "adopt":
            wt = _add_worktree(ctx.root, iid, detach_at=sha)
            smoke = run_smoke(wt)
            _remove_worktree(ctx.root, iid)  # only needed for the test; adopt is a checkout
            rec["diffstat"] = diffstat(ctx.root, head, sha)
            rec["diff_preview"] = diff_preview(ctx.root, head, sha)
        else:
            wt = _add_worktree(ctx.root, iid)
            report = merge_donor_into_worktree(wt, ctx.root, sha)
            rec["merge_report"] = {k: v for k, v in report.items() if k != "conflict"}
            if report["conflict"]:
                _remove_worktree(ctx.root, iid)
                rec.update({"status": "conflict", "error": report["conflict"], "evolution_command": evolution_command_for_harness(rec)})
                save_import(rec)
                session_event(ctx, sid, actor="system", kind="import.conflict", import_id=iid, note="3-way apply conflicted; delegated to the evolution agent")
                projects.event("import.failed", import_id=iid, reason="conflict")
                return rec
            subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True, capture_output=True, timeout=60)
            if not subprocess.run(["git", "status", "--porcelain"], cwd=str(wt), capture_output=True, text=True, timeout=60).stdout.strip():
                _remove_worktree(ctx.root, iid)
                raise ImportError_(409, "your harness already contains everything that version evolved; nothing to merge")
            subprocess.run(["git", "commit", "-q", "-m", f"import: merge {rec['owner_name']}'s harness version {version_id}"], cwd=str(wt), check=True, capture_output=True, timeout=60)
            merged = _head(wt)
            rec["merged_sha"] = merged
            smoke = run_smoke(wt)
            rec["diffstat"] = diffstat(ctx.root, head, merged)
            rec["diff_preview"] = diff_preview(ctx.root, head, merged)
    except ImportError_:
        _remove_worktree(ctx.root, iid)
        raise
    rec["smoke"] = smoke
    if smoke.get("ran") and smoke.get("ok") is False:
        _remove_worktree(ctx.root, iid)
        rec.update({"status": "failed", "error": "pre-gate smoke failed; the donor version cannot run on this platform", "evolution_command": evolution_command_for_harness(rec)})
        save_import(rec)
        session_event(ctx, sid, actor="system", kind="import.auto_reject", import_id=iid, note="smoke failed in the pre-gate worktree")
        projects.event("import.failed", import_id=iid, reason="smoke")
        return rec
    what = (
        f"Your active harness becomes {rec['owner_name']}'s version “{rec['summary']}” (a new version node in your own repo). "
        f"Your sessions, memory, library and results are untouched. Switch back any time: activate {rec['rollback_to'].get('version_id') or 'the bootstrap root'}."
        if mode == "adopt"
        else f"{rec['owner_name']}'s harness changes are merged on top of your current version as a new child version; your own evolutions are kept."
    )
    payload = {
        "import_id": iid,
        "kind": "harness",
        "mode": mode,
        "owner": owner,
        "owner_name": rec["owner_name"],
        "version_id": version_id,
        "sha": sha,
        "summary": rec["summary"],
        "rationale": rec["rationale"],
        "from_node": rec.get("from_node"),
        "from_node_title": rec.get("from_node_title"),
        "smoke": {"ran": smoke.get("ran"), "ok": smoke.get("ok"), "tests": smoke.get("tests")},
        "diffstat": rec.get("diffstat", ""),
        "diff_preview": rec.get("diff_preview", ""),
        "risks": risks,
        "donor_skills": skills,
        "include_skills": include,
        "what_happens": what,
        "rollback_to": rec["rollback_to"],
    }
    rid = open_request(ctx, sid, "harness_import", f"Import {rec['owner_name']}'s harness version: {rec['summary']}", payload)
    rec["request_id"] = rid
    save_import(rec)
    return rec


def evolution_command_for_harness(rec: dict) -> str:
    return (
        f"Merge colleague {rec.get('owner_name')}'s harness version {rec.get('version_id')} into this harness. "
        f"Its commit {rec.get('sha')} is already fetched into this repository as {_ref_name(rec.get('owner', ''), rec.get('version_id', ''))}. "
        f"Compute its delta from its bootstrap root (`git diff $(git rev-list --max-parents=0 {rec.get('sha')} | tail -1) {rec.get('sha')} -- scaffold research evolution tools roles prompts`), "
        "apply it on top of the current tree resolving conflicts in favour of keeping BOTH sets of tools/roles, run the smoke tests, and propose the merge with a rationale that lists what was imported."
    )


def _check_head_unchanged(root: Path, rec: dict) -> None:
    expected = (rec.get("rollback_to") or {}).get("sha")
    if expected and _head(root) != expected:
        raise ImportError_(409, "your active version changed since this import was prepared (a switch or an evolution merge landed); request the import again")


def apply_harness_import(ctx: UserContext, rec: dict) -> dict:
    """On approve, under the caller's switch lock (see server._guarded_switch):
    the tenant is idle and no other switch is in flight."""
    root = ctx.root
    iid = rec["id"]
    sid = rec["session_id"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    _check_head_unchanged(root, rec)
    imported_from = {"owner": rec["owner"], "owner_name": rec.get("owner_name"), "version_id": rec["version_id"], "sha": rec["sha"], "from_node": rec.get("from_node"), "from_node_title": rec.get("from_node_title"), "import_id": iid}
    if rec["mode"] == "adopt":
        head = _head(root)
        vid = f"{ts}__import-{_slug(rec.get('owner_name') or rec['owner'], 12)}-{_slug(rec.get('summary') or rec['version_id'], 24)}"
        archive_dir = root / "state" / "archive" / "evolutions" / vid
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            (archive_dir / "diff.patch").write_text(_git("diff", "--binary", head, rec["sha"], repo=root, timeout=120), encoding="utf-8")
        except ImportError_:
            (archive_dir / "diff.patch").write_text("", encoding="utf-8")
        (archive_dir / "rationale.md").write_text(
            f"# Imported {rec.get('owner_name')}'s harness version {rec['version_id']}\n\n{rec.get('rationale', '')}\n\nAdopted as-is (detached checkout). Rollback: activate `{rec['rollback_to'].get('version_id') or rec['rollback_to'].get('sha')}`.\n",
            encoding="utf-8",
        )
        node = archive.record_imported_version(
            root,
            archive_dir=archive_dir,
            sha=rec["sha"],
            rollback_to=rec["rollback_to"],
            summary=f"Imported: {rec.get('summary') or rec['version_id']} (from {rec.get('owner_name')})",
            rationale=rec.get("rationale", ""),
            owner=archive.owner_of(root),
            imported_from=imported_from,
            import_id=iid,
            smoke=rec.get("smoke"),
            origin_session=sid,
        )
        try:
            archive.activate_version(root, node["id"])
        except archive.ArchiveError as e:
            # No phantom version: undo the tag, the manifest and the index entry.
            subprocess.run(["git", "tag", "-d", node["tag"]], cwd=str(root), capture_output=True, timeout=30)
            shutil.rmtree(archive_dir, ignore_errors=True)
            idx = read_json(root / "state" / "archive" / "evolutions" / "index.json", None)
            if isinstance(idx, dict):
                idx["versions"] = [v for v in idx.get("versions") or [] if v.get("id") != node["id"]]
                write_json(root / "state" / "archive" / "evolutions" / "index.json", idx)
            raise ImportError_(409, f"switch failed: {e}")
        copied: list[str] = []
        for s in rec.get("include_skills") or []:
            src = user_root(rec["owner"]) / ".claude" / "skills" / s
            dst = root / ".claude" / "skills" / s
            if src.is_dir() and not dst.exists():
                shutil.copytree(src, dst)
                copied.append(s)
        rec.update({"status": "applied", "result": {"version_id": node["id"], "sha": rec["sha"], "skills_copied": copied}})
    else:
        branch = _branch(iid)
        base = _head(root)
        _git("merge", "--ff-only", branch, repo=root)
        head = _head(root)
        archive_dir = root / "state" / "archive" / "evolutions" / f"{ts}__import-merge-{_slug(rec.get('owner_name') or rec['owner'], 12)}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            (archive_dir / "diff.patch").write_text(_git("diff", "--binary", base, head, repo=root, timeout=120), encoding="utf-8")
        except ImportError_:
            pass
        (archive_dir / "rationale.md").write_text(f"# Merged {rec.get('owner_name')}'s harness version {rec['version_id']}\n\n{rec.get('rationale', '')}\n", encoding="utf-8")
        node = archive.record_merged_version(
            root,
            archive_dir=archive_dir,
            head_sha=head,
            base_sha=base,
            summary=f"Merged: {rec.get('summary') or rec['version_id']} (from {rec.get('owner_name')})",
            rationale=rec.get("rationale", ""),
            owner=archive.owner_of(root),
            smoke=rec.get("smoke"),
            origin_session=sid,
        )
        archive.annotate_version(root, node["id"], imported_from=imported_from, import_id=iid)
        _remove_worktree(root, iid)
        rec.update({"status": "applied", "result": {"version_id": node["id"], "sha": head}})
    save_import(rec)  # the apply landed; provenance is best-effort from here on
    try:
        _record_provenance(ctx, rec)
    except Exception:
        pass
    session_event(ctx, sid, actor="system", kind="import.applied", import_id=iid, version=rec["result"]["version_id"])
    projects.event("import.applied", import_id=iid, user=ctx.user.user_id, version=rec["result"]["version_id"])
    return rec


def _record_provenance(ctx: UserContext, rec: dict) -> None:
    """Provenance on both nodes + a confirmed shares_method edge."""
    entry = {"import_id": rec["id"], "kind": rec["kind"], "mode": rec.get("mode"), "owner": rec["owner"], "version_id": rec["version_id"], "tools": rec.get("tools") or [], "version": (rec.get("result") or {}).get("version_id"), "ts": now_str()}
    mine = projects.load_node(rec.get("node_id") or "") if rec.get("node_id") else None
    if mine and projects.is_owner(mine, ctx.user.user_id):
        mine.setdefault("links", {}).setdefault("imports", []).append(entry)
        projects._refresh_harness_links(mine, projects.harness_inventory(ctx.root))
        projects.save_node(mine)
    donor = projects.load_node(rec.get("from_node") or "") if rec.get("from_node") else None
    if donor:
        donor.setdefault("links", {}).setdefault("exports", []).append({**entry, "by": ctx.user.user_id})
        projects.save_node(donor)
    if mine and donor:
        e = projects._write_edge(mine["id"], donor["id"], "shares_method", weight=1.0, source="import", status="confirmed", rationale=f"Imported {rec['kind']} {rec.get('summary') or ', '.join(rec.get('tools') or [])} from this problem's harness.")
        projects.event("edge.confirmed", edge=e["id"], src=e["src"], dst=e["dst"], type="shares_method", source="import")


def reject_import(ctx: UserContext, rec: dict, note: str | None = None) -> dict:
    _remove_worktree(ctx.root, rec["id"])
    drop_ref(ctx.root, rec["owner"], rec["version_id"])
    rec.update({"status": "rejected", "note": note or ""})
    save_import(rec)
    session_event(ctx, rec["session_id"], actor="system", kind="import.rejected", import_id=rec["id"], note=note or "")
    projects.event("import.rejected", import_id=rec["id"], user=ctx.user.user_id)
    return rec


# ---------- tool import ----------


def prepare_tool_import(
    ctx: UserContext,
    *,
    owner: str,
    version_id: str,
    tools: list[str],
    roles: list[str],
    node_id: str | None,
    rec_id: str | None,
) -> dict:
    uid = ctx.user.user_id
    if owner == uid:
        raise ImportError_(400, "those tools are already in your own repository")
    tools = [t for t in dict.fromkeys(t.strip() for t in tools or []) if re.fullmatch(r"[A-Za-z0-9_]{1,64}", t)]
    if not tools:
        raise ImportError_(400, "pick at least one tool")
    roles = [r for r in dict.fromkeys(r.strip() for r in roles or []) if re.fullmatch(r"[A-Za-z0-9_]{1,64}", r)]
    if not roles:
        raise ImportError_(400, "pick at least one role to wire the tool into")
    if not projects.version_discoverable(owner, version_id):
        raise ImportError_(403, "that version is not shared")
    if pending_imports(uid, ctx):
        raise ImportError_(409, "an import is already awaiting your approval; decide it first")
    for r in roles:
        if _role_yaml_path(ctx.root, r) is None:
            raise ImportError_(404, f"you have no role named {r!r}")
    mine = set(projects._tool_names_in(ctx.root))
    dup = [t for t in tools if t in mine]
    if dup:
        raise ImportError_(409, f"you already have: {', '.join(dup)}")
    iid = new_import_id()
    sid = session_id_for(iid)
    names = projects._display_names()
    rec: dict[str, Any] = {
        "id": iid,
        "kind": "tool",
        "user_id": uid,
        "owner": owner,
        "owner_name": names.get(owner, owner),
        "version_id": version_id,
        "sha": None,
        "tools": tools,
        "roles": roles,
        "summary": ", ".join(tools),
        "node_id": node_id,
        "from_node": None,
        "rec_id": rec_id,
        "session_id": sid,
        "request_id": None,
        "status": "preparing",
        "created": now_str(),
    }
    save_import(rec)
    try:
        return _prepare_tool_import(ctx, rec)
    except Exception as e:
        if rec.get("status") == "preparing":
            rec.update({"status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:600]}"})
            save_import(rec)
            _remove_worktree(ctx.root, iid)
            drop_ref(ctx.root, owner, version_id)
        raise


def _prepare_tool_import(ctx: UserContext, rec: dict) -> dict:
    uid = ctx.user.user_id
    iid, sid, owner, version_id, tools, roles = rec["id"], rec["session_id"], rec["owner"], rec["version_id"], rec["tools"], rec["roles"]
    prune_stale_worktrees(ctx.root)
    sha = fetch_version(ctx.root, owner, version_id)
    available = set(projects.tools_at(user_root(owner), sha))
    missing = [t for t in tools if t not in available]
    if missing:
        drop_ref(ctx.root, owner, version_id)
        raise ImportError_(404, f"not in that version: {', '.join(missing)}")
    head = _head(ctx.root)
    rec.update({
        "sha": sha,
        "status": "pending",
        "rollback_to": {"sha": head, "version_id": archive.list_versions(ctx.root).get("active")},
    })
    for n in projects.all_nodes():
        if projects.is_owner(n, owner) and any(v.get("id") == version_id for v in ((n.get("links") or {}).get("harness_versions") or [])):
            rec["from_node"] = n["id"]
            rec["from_node_title"] = (n.get("statement") or {}).get("title")
            break
    _session_dir(ctx, sid)
    session_event(ctx, sid, actor="human", kind="import.requested", import_id=iid, kind_detail="tool", owner=owner, version_id=version_id, tools=tools, roles=roles)
    projects.event("import.requested", import_id=iid, import_kind="tool", user=uid, owner=owner, version_id=version_id, tools=tools)
    wt = _add_worktree(ctx.root, iid)
    try:
        for t in tools:
            _git("checkout", sha, "--", f"tools/{t}", repo=wt)
        for r in roles:
            p = _role_yaml_path(wt, r)
            if p is None:
                continue
            text = p.read_text(encoding="utf-8")
            for t in tools:
                text, _ = add_tool_to_role_yaml(text, t)
            p.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "commit", "-q", "-m", f"import tools {', '.join(tools)} from {rec['owner_name']}@{version_id}"], cwd=str(wt), check=True, capture_output=True, timeout=60)
        merged = _head(wt)
        rec["merged_sha"] = merged
        paths = [f"tools/{t}" for t in tools] + ["roles"]
        rec["diffstat"] = diffstat(ctx.root, head, merged, *paths)
        rec["diff_preview"] = diff_preview(ctx.root, head, merged, *paths)
        smoke = run_smoke(wt)
    except (subprocess.CalledProcessError, ImportError_) as e:
        _remove_worktree(ctx.root, iid)
        msg = getattr(e, "output", None) or getattr(e, "stderr", None) or str(e)
        raise ImportError_(500, f"could not stage the tool import: {str(msg)[-600:]}")
    rec["smoke"] = smoke
    if smoke.get("ran") and smoke.get("ok") is False:
        _remove_worktree(ctx.root, iid)
        rec.update({"status": "fallback", "error": "smoke failed: the tool needs scaffold changes from the donor", "evolution_command": evolution_command_for_tools(rec)})
        save_import(rec)
        session_event(ctx, sid, actor="system", kind="import.fallback", import_id=iid, note="smoke failed after the subset checkout; an evolution command was prepared")
        projects.event("import.failed", import_id=iid, reason="smoke-fallback")
        return rec
    payload = {
        "import_id": iid,
        "kind": "tool",
        "owner": owner,
        "owner_name": rec["owner_name"],
        "version_id": version_id,
        "sha": sha,
        "tools": tools,
        "roles": roles,
        "from_node": rec.get("from_node"),
        "from_node_title": rec.get("from_node_title"),
        "smoke": {"ran": smoke.get("ran"), "ok": smoke.get("ok"), "tests": smoke.get("tests")},
        "diffstat": rec.get("diffstat", ""),
        "diff_preview": rec.get("diff_preview", ""),
        "what_happens": f"tools/{', tools/'.join(tools)} from {rec['owner_name']}'s version are added to your harness and wired into {', '.join(roles)}; this lands as a new child version of your current one (fast-forward). Rollback: activate the previous version.",
        "rollback_to": rec["rollback_to"],
    }
    rid = open_request(ctx, sid, "tool_import", f"Import tool{'s' if len(tools) > 1 else ''} {', '.join(tools)} from {rec['owner_name']}", payload)
    rec["request_id"] = rid
    save_import(rec)
    return rec


def evolution_command_for_tools(rec: dict) -> str:
    tools = rec.get("tools") or []
    roles = rec.get("roles") or []
    return (
        f"Import the tool package{'s' if len(tools) > 1 else ''} {', '.join(tools)} from colleague {rec.get('owner_name')}'s harness version {rec.get('version_id')} "
        f"(commit {rec.get('sha')}, already fetched into this repository as {_ref_name(rec.get('owner', ''), rec.get('version_id', ''))}). "
        f"Check the package(s) out from that commit (`git checkout {rec.get('sha')} -- " + " ".join(f"tools/{t}" for t in tools) + "`), "
        f"add each tool to the `tools:` list of the role(s) {', '.join(roles)}, then make the smoke tests pass — the plain checkout failed them, so the tool depends on scaffold changes present at the donor commit (inspect `git diff HEAD {rec.get('sha')} -- scaffold/ research/`); port only what the tool needs. Run the tests and propose the merge."
    )


def apply_tool_import(ctx: UserContext, rec: dict) -> dict:
    """On approve, under the caller's switch lock (tenant idle)."""
    root = ctx.root
    iid = rec["id"]
    sid = rec["session_id"]
    _check_head_unchanged(root, rec)
    base = _head(root)
    _git("merge", "--ff-only", _branch(iid), repo=root)
    head = _head(root)
    ts = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = root / "state" / "archive" / "evolutions" / f"{ts}__import-tools-{_slug('-'.join(rec.get('tools') or []), 24)}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        (archive_dir / "diff.patch").write_text(_git("diff", "--binary", base, head, repo=root, timeout=120), encoding="utf-8")
    except ImportError_:
        pass
    (archive_dir / "rationale.md").write_text(f"# Imported tools {', '.join(rec.get('tools') or [])} from {rec.get('owner_name')}'s version {rec['version_id']}\n\nWired into: {', '.join(rec.get('roles') or [])}\n", encoding="utf-8")
    node = archive.record_merged_version(
        root,
        archive_dir=archive_dir,
        head_sha=head,
        base_sha=base,
        summary=f"Imported tools: {', '.join(rec.get('tools') or [])} (from {rec.get('owner_name')})",
        rationale=f"Tool import from {rec.get('owner_name')}'s version {rec['version_id']}; wired into {', '.join(rec.get('roles') or [])}.",
        owner=archive.owner_of(root),
        smoke=rec.get("smoke"),
        origin_session=sid,
    )
    archive.annotate_version(
        root,
        node["id"],
        imported_from={"owner": rec["owner"], "owner_name": rec.get("owner_name"), "version_id": rec["version_id"], "sha": rec["sha"], "from_node": rec.get("from_node"), "from_node_title": rec.get("from_node_title"), "tools": rec.get("tools"), "import_id": iid},
        import_id=iid,
    )
    _remove_worktree(root, iid)
    rec.update({"status": "applied", "result": {"version_id": node["id"], "sha": head, "tools": rec.get("tools")}})
    save_import(rec)
    try:
        _record_provenance(ctx, rec)
    except Exception:
        pass
    session_event(ctx, sid, actor="system", kind="import.applied", import_id=iid, version=node["id"], tools=rec.get("tools"))
    projects.event("import.applied", import_id=iid, user=ctx.user.user_id, version=node["id"], tools=rec.get("tools"))
    return rec


# ---------- the answer hook ----------


def import_for_request(ctx: UserContext, sid: str, request_id: str) -> dict | None:
    """The pending ledger record an evo-import-* HITL request belongs to."""
    iid = import_id_from_session(sid)
    try:
        rec = load_import(iid) if iid else None
    except Exception:
        return None
    if not rec or rec.get("user_id") != ctx.user.user_id or rec.get("status") != "pending":
        return None
    if rec.get("request_id") and request_id != rec["request_id"]:
        return None
    return rec


def on_answer(ctx: UserContext, rec: dict, decision: str, note: str | None, *, guarded) -> dict:
    """Called by POST /hitl/{sid}/{rid}/answer for evo-import-* sessions after
    the answer is recorded. ``guarded(fn)`` runs ``fn`` under the R17 switch
    lock with the busy check (the route refuses before consuming the request
    if the tenant is busy). Never raises — a failure lands on the ledger
    (status failed) and in the session events."""
    iid = rec["id"]
    sid = rec["session_id"]
    try:
        if decision != "approve":
            return reject_import(ctx, rec, note)
        session_event(ctx, sid, actor="human", kind="import.approved", import_id=iid)
        projects.event("import.approved", import_id=iid, user=ctx.user.user_id)
        if rec["kind"] == "harness":
            return guarded(lambda: apply_harness_import(ctx, rec))
        return guarded(lambda: apply_tool_import(ctx, rec))
    except Exception as e:  # noqa: BLE001
        rec.update({"status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:800]}"})
        save_import(rec)
        _remove_worktree(ctx.root, iid)
        session_event(ctx, sid, actor="system", kind="import.failed", import_id=iid, error=rec["error"])
        projects.event("import.failed", import_id=iid, error=rec["error"])
        return rec
