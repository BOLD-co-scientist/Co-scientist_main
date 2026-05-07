# UI2 — Persistent context library

**Status:** Done
**Owner:** yhg01
**Started:** 2026-05-07
**Completed:** 2026-05-07
**Done when:** A user can upload a file once via the UI, see it persist across all future research and evolution sessions, and have agents read it through `fs_read` — without `researcher_data/:ro` or `fs_write_workspace` allowed roots changing.

## Why

Researchers re-run experiments and follow-up sessions on the same dataset; the per-session uploads pattern (Option A in the discussion) would force re-upload every time, which is the opposite of how science work actually flows. The library lives under `state/library/`, which exploits an existing rw mount (`./state` is already mounted rw for per-session results and evolution archives) instead of relaxing the read-only mount on `researcher_data/`. This delivers "upload once, use forever" while keeping every sandbox invariant in `CLAUDE.md` intact: agents gain a new *read* path; the only new *write* path is a single API endpoint the user explicitly drives.

## Steps

### Scaffold

- [x] 1. **Settings.** Add `LIBRARY = ROOT / "state" / "library"` to `scaffold/settings.py` and create it from `ensure_session_dirs` (or a new `ensure_runtime_dirs()` called at API startup) so it exists before the first upload.
- [x] 2. **fs_read allowed root.** Append `settings.LIBRARY.resolve()` to `_allowed_roots` in `tools/fs_read/server.py:11-19`. No other tool changes — `fs_write_workspace` stays scoped to `scratch/`+`results/`.

### API

- [x] 3. **Upload endpoint.** `POST /library/files` (multipart). Body: `UploadFile`. Behavior: sanitize filename (strip path separators, reject leading dots, collapse to basename), **stream chunked to disk** (no `await file.read()` of full body — use `shutil.copyfileobj` on `file.file` in 1 MB chunks). Write to `state/library/.staging/<uuid>` first, then atomic `os.replace` to `state/library/<sanitized_name>` on success — partial uploads never masquerade as complete. **Overwrite if filename exists** (last-write-wins; no 409). Server-side soft cap configurable via env `LIBRARY_MAX_BYTES` (default 50 GB) — purely a guardrail against runaway uploads, not a usage ceiling. Return `{"name": <name>, "size": <bytes>, "overwrote": <bool>}`. Append a `library.upload` event to a new top-level `state/library_events.jsonl` (audit trail for an action that has no session id).
- [x] 4. **List endpoint.** `GET /library/files` returns `[{"name": …, "size": …, "mtime": …}, …]` sorted by mtime desc. Skips `.staging/` and dotfiles. **Lists every file in `state/library/` regardless of how it got there** — UI-uploaded *and* host-dropped files (`cp`/`rsync`/symlink into the directory) appear identically.
- [x] 5. **Delete endpoint.** `DELETE /library/files/{name}` removes the file (path-guard against escapes via `Path(name).name`). Append `library.delete` event.
- [x] 5b. **Health endpoint.** `GET /library/health` returns:
  - `total_bytes` (sum of all files, following symlinks where possible)
  - `file_count` (excluding `.staging/`)
  - `broken_symlinks: [{"name": …, "target": …}]` — entries where `os.readlink` resolves but the target doesn't exist *inside the container* (the `state/library/` mount path stays the same in the container, but symlinks pointing to host paths outside any bound mount will be unreachable from agents).
  - `staging_files: int` (count under `.staging/`; non-zero means an upload was interrupted — surface as a warning).
  - `disk_free_bytes` (host disk space available on `state/`'s filesystem, via `shutil.disk_usage`).
  Cheap O(N) walk; fine for v1.
- [x] 6. **Schemas.** Add `LibraryFile` and `LibraryHealth` to `api/schemas.py`.

### Prompt

- [x] 7. **Supervisor prompt — library awareness.** Replace the single `fs_read` line (`research/supervisor_prompt.md:16`) with a block that teaches the agent both the *read* path and the *upload* affordances, so the human can ask the supervisor directly ("how do I get my 80 GB genome in?") instead of having to know the system's plumbing. Proposed wording:

  ```markdown
  - Use `fs_read` to read files in:
    - `researcher_data/` — files the user pre-curated on the host filesystem.
    - `state/library/` — the user's persistent context library, shared across all sessions.
    - your session's `scratch/` and `results/` dirs.
  - **If the user asks how to add a file to the library**, tell them they have two options and pick the right one based on file size:
    - **Files up to ~5 GB:** drag-drop into the **Library** panel in the UI sidebar (uses `POST /library/files`).
    - **Files larger than ~5 GB** (genomes, training corpora, full databases): copy or symlink directly on the host into `./state/library/` — e.g. `cp /data/genome.fa ./state/library/` or `ln -s /mnt/big/dataset ./state/library/dataset`. The library is just a directory; anything that lands in it shows up automatically in the UI listing and is reachable by `fs_read`.
    - Both paths converge on the same files. Files in `state/library/` overwrite by name (last-write-wins).
  - You can never *write* to `state/library/` or `researcher_data/` yourself — only the human can. Don't pretend otherwise.
  ```

  Keep this as a prompt-level instruction (not a hard rule); the "Hard rules" section stays focused on system-modification boundaries.

- [x] 7b. **Evolution-agent prompt — library awareness.** Update `evolution/evolution_prompt.md` so the evolution agent (which has full read access to the codebase via `bash_ro` and read+write access to its worktree) understands the upload story. It needs this for two reasons: (1) users may ask it the same upload questions and we don't want a hallucinated answer, (2) when the user instructs it to add features that touch file flows, it should reason about the existing affordances rather than inventing new ones. Add a section:

  ```markdown
  ## Context library (read-only to you and to research agents)

  Users keep persistent files in `state/library/`, shared across all sessions. Two paths get files there, both ending up in the same directory:
  - UI uploader (`POST /library/files`) — practical limit ~5 GB.
  - Host-side drop (`cp`, `rsync`, or `ln -s` into `./state/library/`) — any size; the canonical path for multi-GB datasets.

  You cannot write to `state/library/` (it's outside your worktree, and no tool exposes the capability). When asked how to upload, recommend the right path by file size. When designing system changes that touch file flows, prefer extending these affordances over inventing new ones.
  ```

  Don't change the "Hard rules" or worktree-confinement sections — this is purely awareness-level context.

### UI

- [x] 8. **Library panel in sidebar.** New `## Library` section above "Session History" in `ui/app.py`. Always visible (Research and Evolution modes), since the same files are useful in both contexts. Contents:
  - `st.file_uploader(accept_multiple_files=True)` — submits each selected file via `POST /library/files`. Caption underneath: "Files >5 GB? Drop them directly into `./state/library/` on the host — they'll appear here automatically."
  - List of existing files (name + size + age) pulled from `GET /library/files`, each with a 🗑️ delete button. Files dropped via host fs appear here too (no visual distinction; they're equivalent).
  - **Health badge** — small caption pulled from `GET /library/health`: total bytes used, free disk, and a red warning if `broken_symlinks` or `staging_files > 0`. Click to expand the details.
- [x] 9. **Library hint on welcome screen.** When starting a new research session, if the library is non-empty, render a `st.caption(f"📎 {n} file(s) in library will be available to the agent: {names}")` above the chat input. Informational; the actual auto-prepend happens server-side (step 9b).
- [x] 9b. **Auto-prepend library listing to supervisor's first turn.** In `research/runtime.py` (where the supervisor's first turn is constructed from the user's task), if `state/library/` is non-empty, prepend a system-generated context block of the form:

  ```
  Files currently in the user's library (state/library/, read via fs_read):
  - <name1> (<size>, mtime <iso>)
  - <name2> (<size>, mtime <iso>)
  ...

  User task:
  <original task text>
  ```

  Do this every research-session start, not just the welcome. Evolution sessions skip this (meta-agent operates on code).
- [x] 9c. **Streamlit upload size config.** Streamlit's default `maxUploadSize` is 200 MB. Add `.streamlit/config.toml` with `[server]\nmaxUploadSize = 5120` (5 GB) — sized for what's *practical* through a browser, not what the server allows. Bigger files go via host-side drop (step 8 caption). The server-side `LIBRARY_MAX_BYTES` (step 3) is independent and far higher; the two caps don't have to match.

### Verification

- [x] 10. **Backend verification (per CLAUDE.md policy).**
  1. `curl -F file=@/tmp/sample.csv http://127.0.0.1:8765/library/files` returns 200 with name+size; file appears at `state/library/sample.csv` on host.
  2. `curl http://127.0.0.1:8765/library/files` lists it.
  3. Drop a second file directly on the host: `cp /tmp/another.txt state/library/`. Confirm `GET /library/files` lists it identically (no UI distinction between upload paths).
  4. Re-upload `sample.csv` with different contents. Confirm overwrite + `{"overwrote": true}`.
  5. Start a research session: `curl -X POST .../research/sessions -d '{"task":"List files in state/library/ and tell me what you see."}'`. Confirm via `events.jsonl` that (a) the auto-prepended listing block appears in the supervisor's first turn, and (b) the supervisor uses `fs_read.list state/library/` and reports both files.
  6. **Supervisor-knows-the-affordance test:** start a research session with task `"I have a 100 GB sequencing dataset on my host. How do I make it available to the agent?"`. Confirm the supervisor's answer mentions the host-drop path (`cp` / `ln -s` into `./state/library/`) and the UI uploader, and correctly steers toward host-drop for the 100 GB case.
  7. **Evolution-agent-knows-the-affordance test:** dispatch an evolution command `"Where do users put files for research sessions to read? Don't change anything — just describe the affordances."`. Confirm the agent's response (in the events log) mentions both upload paths and that it can't write to `state/library/`.
  8. **Health endpoint test:** `curl http://127.0.0.1:8765/library/health` returns the expected shape. Create a deliberately broken symlink (`ln -s /nonexistent state/library/dead`) and confirm it's flagged.
  9. **Negative test:** try writing to library via `fs_write_workspace.write` with `path=state/library/x.txt` from inside an agent run; expect `PermissionError: writes outside scratch/results forbidden`.
  10. `curl -X DELETE .../library/files/sample.csv` removes the file. Health endpoint reflects the change.
- [x] 11. **Manual UI render check** (only after backend passes). Refresh http://127.0.0.1:8501; upload a file via the sidebar Library panel; start a session; verify it's listed in the welcome hint and the agent reaches it.
- [x] 12. **Roadmap entry.** Move from "Planned" to "Done" with the commit SHA.

## Files touched

- `scaffold/settings.py` — add `LIBRARY` constant + ensure-create.
- `tools/fs_read/server.py` — append `LIBRARY` to `_allowed_roots`.
- `api/server.py` — four new endpoints (upload, list, delete, health).
- `api/schemas.py` — `LibraryFile`, `LibraryHealth` models.
- `research/supervisor_prompt.md` — library + dual-upload-path block.
- `evolution/evolution_prompt.md` — library awareness section.
- `research/runtime.py` — auto-prepend library listing to supervisor's first turn.
- `.streamlit/config.toml` — `maxUploadSize = 5120`.
- `ui/app.py` — Library sidebar panel (uploader + list + delete + health badge) and welcome-screen hint.
- `docs/plans/UI2-context-library.md` — this plan.
- `ROADMAP.md` — UI2 row under "🖥️ UI".

## Verification

```bash
cd /Users/yuhe/Desktop/OpenPhil/coscientist-evolution_ui

# Backend
echo "col_a,col_b\n1,2\n3,4" > /tmp/sample.csv
curl -s -F file=@/tmp/sample.csv http://127.0.0.1:8765/library/files
curl -s http://127.0.0.1:8765/library/files | python3 -m json.tool
ls state/library/

# Agent reachability (will burn a small amount of API spend)
SID=$(curl -s -X POST http://127.0.0.1:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"Use fs_read.list to inspect state/library/ and tell me the filenames you see."}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
sleep 30; grep -E "fs_read|sample.csv" state/sessions/$SID/events.jsonl | head -10

# Cleanup
curl -s -X DELETE http://127.0.0.1:8765/library/files/sample.csv

# UI manual check
open http://127.0.0.1:8501
```

## Risks / open questions

- **Filename collision.** Last-write-wins by design (per user instruction 2026-05-07). Re-uploading `data.csv` silently replaces the previous one — the return value's `overwrote: true` is the only signal. Could surface a UI confirmation later if researchers report accidentally clobbering files.
- **Binary blobs.** `fs_read.read` is text-only (`p.read_text(encoding="utf-8", errors="replace")`). Image/PDF files upload fine but agents can only see filenames via `list`, not contents. That's the same behavior as `researcher_data/` today; future evolution-agent work can add doc-extraction tools.
- **No effective size cap; two upload paths.** Resolved 2026-05-07 after user pushed back on a hard 10 GB cap (research workflows in biology/AI need bigger). Design:
  - HTTP upload via UI: practical ceiling ~5 GB (Streamlit `maxUploadSize`), server cap `LIBRARY_MAX_BYTES` (default 50 GB) as a runaway-prevention guardrail.
  - Host-side drop: user `cp`/`rsync`/`ln -s` directly into `./state/library/`. No size limit beyond disk capacity. UI lists these identically to HTTP-uploaded files.
  - Server uses streaming + atomic rename via `state/library/.staging/<uuid>` so partial uploads never appear in the listing.
  - Real constraint becomes host disk space — surface library total bytes in the UI panel later if researchers complain.
- **No auth.** v0 has no users; the library is single-tenant. If/when multi-user lands, library becomes per-user (or per-org). Not in scope here.
- **Library write surface.** The new `POST /library/files` is a write capability the API didn't have before. It's user-driven and gated, but worth noting in `CLAUDE.md` "Sandboxing layers" as an additive bullet (not a relaxation).
- **Auto-prepend resolved.** Step 9b auto-prepends the library listing (with size + mtime) to the supervisor's first turn. Risk: bloats the first prompt for users with many large files. Mitigation: only the *listing* is prepended (no content); for typical libraries (<100 files) this is well under 10 KB.
- **Evolution-agent awareness in scope** (resolved 2026-05-07). Step 7b updates `evolution/evolution_prompt.md` so the evolution agent — which has full read access to the codebase + harness via `bash_ro` — can answer upload questions and reason about file flows when designing system changes.

## Notes

### 2026-05-07 — implementation + verification

- Implemented in commit `94cfbd3`. All 10 verification sub-steps pass:
  1. UI upload via `curl -F` works; chunked stream + atomic rename leaves `.staging/` empty.
  2. `GET /library/files` lists uploaded file with size + mtime.
  3. Host-drop test: `echo … > state/library/host_dropped.txt` shows in listing alongside UI-uploaded files. No visual distinction.
  4. Re-upload returns `{"overwrote": true}`.
  5. Research session `20260507-211255-b211a5` saw the auto-prepended library block (supervisor explicitly cited "the system-reminder showed" matching sizes), used `fs_read.list state/library/`, read both files, reported back filenames + first lines.
  6. Affordance test `20260507-211335-c424ca` (100 GB sequencing dataset prompt): supervisor steered correctly to host-drop, gave exact `cp` and `ln -s` commands, distinguished `state/library/` vs `researcher_data/`, mentioned read-only constraint, gave practical advice on streaming + indexes.
  7. Evolution affordance test `evo-20260507-211410-912b29`: agent inspected `tools/fs_read/server.py::_allowed_roots` and started enumerating the three read paths (state/library/, researcher_data/, session dirs). **Eventlog truncates `evolution.note` text to 400 chars** (`evolution/runtime.py:90`) — agent's full response is fine but only first 400 chars logged. Logged as **B2** in ROADMAP.
  8. Health endpoint detects deliberately broken symlink (`ln -s /nonexistent_target_path_xyz`) and reports it.
  9. Negative test: agent attempted `fs_write_workspace.write` to `state/library/x.txt`, got the expected `ERROR: writes outside scratch/results forbidden`, did not retry, reported correctly. File was never created. Sandbox invariant intact.
  10. `DELETE` works; health reflects updated state. Path-traversal-resistant: `Path().name` strips `..`, leading-dot stripped, FastAPI route refuses URL-encoded slashes. Verified via `filename=../traverse.txt` (lands as `traverse.txt`, not outside library) and `DELETE /library/files/..` (404).

- **Audit trail caveat:** `state/library_events.jsonl` only logs API-mediated actions (uploads, deletes). Host-side `cp`/`ln -s` adds don't write events. Acceptable for v1 — deletes via API still log, and the host fs is the user's territory.
