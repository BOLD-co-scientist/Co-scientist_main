# R13 — LaTeX-by-default final output (with PDF compilation)

**Status:** Done (backend-verified, incl. live agent run)
**Owner:** Claude (feat/R13-latex-output)
**Started:** 2026-07-09
**Done when:** With `OUTPUT_FORMAT=latex` (the new default), a research session writes its final deliverable to `state/sessions/<sid>/results/<name>.tex` and a compiled `results/<name>.pdf` alongside it; setting `OUTPUT_FORMAT=markdown` restores the current `.md` behavior with zero code edits.

## Why
The deliverables the coscientist produces are scientific reports — equations, tables, and figures render far better as LaTeX/PDF than as markdown, and PDF is the artifact a researcher actually keeps or shares. Output format today is hardcoded in one prompt line (`research/supervisor_prompt.md:26`, "markdown preferred"). We make LaTeX the default, keep it switchable via an env-driven setting (matching the `config_loader` `{{PLACEHOLDER}}` pattern), and add a compile tool + TeX toolchain so `.tex` → `.pdf` happens in-container.

## Design (decisions locked with the human)
- **Scope:** full compilable `.tex` documents **and** a compile-to-PDF step (not inline-math-in-markdown).
- **Toggle:** env-driven `OUTPUT_FORMAT` (`latex` | `markdown`), rendered into prompts via a `{{OUTPUT_FORMAT_GUIDANCE}}` placeholder. Default `latex`.
- **Compile surface:** a new **explicit** MCP tool `latex_compile.render(path)` (not auto-compile on every `.tex` write). The supervisor prompt instructs the agent to call it as the final step. Path-guarded to `results/` only, mirroring `fs_write_workspace`.
- **TeX toolchain:** installed via `apt-get` in the Dockerfile's existing root stage (before `USER myuser`), so runtime stays non-root (Rule 1 preserved). Use a scoped texlive set, not full `texlive` (size).

## Steps
Ordered checklist. The first `- [ ]` is "current". Tick as you finish, in order.

- [x] 1. **Settings.** Add `OUTPUT_FORMAT = os.environ.get("COSCIENTIST_OUTPUT_FORMAT", "latex").lower()` to `scaffold/settings.py` (validate ∈ {`latex`,`markdown`}, fall back to `latex` on garbage). Document the env var next to the others.
- [x] 2. **Guidance strings.** Create `prompts/output_format/latex.md` and `prompts/output_format/markdown.md` — each a short block describing the required deliverable format for that mode (LaTeX: emit a self-contained `\documentclass{article}` doc to `results/<name>.tex`, then call `latex_compile.render` to produce the PDF; Markdown: the current "human-readable, markdown preferred" guidance). A tiny loader picks the file by `settings.OUTPUT_FORMAT`.
- [x] 3. **Wire the placeholder.** In `research/runtime.py:_build_options`, load the guidance block for the active mode and pass it into `config_loader.render_prompt(sup, ..., OUTPUT_FORMAT_GUIDANCE=<block>)`. Replace the hardcoded line at `research/supervisor_prompt.md:26` with a `{{OUTPUT_FORMAT_GUIDANCE}}` placeholder (keep the "write under results/ via fs_write_workspace" instruction; only the *format* part becomes dynamic).
- [x] 4. **Data-analyst prompt.** Update `prompts/data_analyst.md:9` ("markdown summaries") to be format-neutral / reference the active output format, so subagent-written summaries match the session default. (Keep it light — the supervisor owns the final deliverable.)
- [x] 5. **New tool: `latex_compile`.** Create `tools/latex_compile/{__init__.py,server.py}` exposing `render(path)`:
      - Resolve `path` with a `results/`-only path-guard (copy the `_resolve_safe` pattern from `tools/fs_write_workspace/server.py`; **only** `results/` is allowed — no `scratch/`, no traversal).
      - Require the target to be an existing `.tex` file.
      - Run the compiler in a subprocess with `setrlimit` CPU/mem caps + minimal env (mirror `tools/py_exec/server.py`), `cwd` = the file's dir, non-interactive (`-interaction=nonstopmode -halt-on-error`), two passes for refs. Prefer `latexmk -pdf` if present, else `pdflatex` ×2.
      - On success return the `.pdf` path + size; on failure return the last N lines of the `.log` (truncated) as an error, never raising. Clean aux files.
- [x] 6. **Register the tool.** Add an explicit `latex_compile` branch to `scaffold/tools_registry.build_tools` (the generic loader would already find it, but explicit matches the other first-class tools). Add `latex_compile` to `roles/supervisor.yaml` `tools:` and `roles/subagents/data_analyst.yaml` `tools:`.
- [x] 7. **Dockerfile: TeX toolchain.** Add `texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended latexmk` to the existing root `apt-get install` block (the one before the `myuser` creation). Do **not** move or duplicate the `USER myuser` switch; verify `docker exec <c> id` is still non-root and `tests/test_smoke_v0.py::test_dockerfile_runs_nonroot` passes.
- [x] 8. **Tests.** Add `tests/test_latex_output.py`: (a) settings default is `latex`; `OUTPUT_FORMAT=markdown` env flips it; garbage falls back. (b) guidance loader returns the right block per mode. (c) `latex_compile._resolve_safe` rejects `scratch/`, `../`, and absolute escapes; accepts a `results/` path. (d) [env-gated, skip if no `pdflatex`] compile a 5-line hello-world `.tex` → assert a non-empty `.pdf` appears. Keep the compile test out of the always-on smoke gate (toolchain may be absent on host).
- [x] 9. **Backend verification.** `docker compose up -d coscientist-api`; POST a research session whose task asks for a short report; tail `events.jsonl`; assert `results/` ends up with both a `.tex` and a `.pdf`. Then re-run with `COSCIENTIST_OUTPUT_FORMAT=markdown` and assert a `.md` (no compile). Record commands + outcome under **Notes**.
- [x] 10. **Docs.** Note the toggle + PDF artifact in `CLAUDE.md` "Common commands" / v0 status, and add the env var to `.env.example` if one exists.

## Files touched
- `scaffold/settings.py` — new `OUTPUT_FORMAT` setting (+ env doc).
- `prompts/output_format/latex.md`, `prompts/output_format/markdown.md` — new guidance blocks.
- `research/supervisor_prompt.md` — line 26 → `{{OUTPUT_FORMAT_GUIDANCE}}`.
- `research/runtime.py` — load + pass the guidance block into `render_prompt`.
- `prompts/data_analyst.md` — format-neutral summary wording.
- `tools/latex_compile/__init__.py`, `tools/latex_compile/server.py` — new compile tool (new).
- `scaffold/tools_registry.py` — explicit `latex_compile` branch.
- `roles/supervisor.yaml`, `roles/subagents/data_analyst.yaml` — add `latex_compile` to `tools`.
- `Dockerfile` — texlive + latexmk in the root apt stage.
- `tests/test_latex_output.py` — new tests.
- `CLAUDE.md`, `.env.example` — doc the toggle.

## Verification
- `PYTHONPATH=. python3 -m pytest tests/test_latex_output.py tests/test_smoke_v0.py -q` (host).
- `python3 -c "import ast,glob; [ast.parse(open(f).read()) for f in ['scaffold/settings.py','research/runtime.py','tools/latex_compile/server.py','scaffold/tools_registry.py']]"`.
- Curl-driven backend pass (step 9): a session in `latex` mode yields `results/*.tex` + `results/*.pdf`; a session in `markdown` mode yields `results/*.md`.
- `docker exec <container> id` shows non-root; `test_dockerfile_runs_nonroot` green.

## Risks / open questions
- **Minimal-scaffold tension.** Choosing the *default* output format is arguably prompt/methodology work the evolution agent could own. What's legitimately developer-side here is the *plumbing*: a new tool, a Docker dependency, and a config toggle. We ship the mechanism + a sane default and leave finer formatting choices (templates, journal styles) to the evolution agent. Flag for the human before over-investing in LaTeX styling.
- **Image size.** `texlive-latex-extra` pulls in a lot (~1–2 GB). If that's too heavy for the FLAIR image, fall back to `texlive-latex-recommended` only, or evaluate `tectonic` — but tectonic fetches packages on first run and the container has **no runtime internet**, so it'd need a pre-warmed package cache baked into the image. Decide during step 7.
- **Compile failures are common** (missing packages, bad LaTeX from the model). The tool must return the `.log` tail as a clean error so the agent can self-correct, and the supervisor prompt should say "if compile fails, fix the `.tex` and retry once, then fall back to shipping the `.tex` + the error." Never let a compile failure lose the written `.tex`.
- **UI rendering.** Streamlit can't render `.tex`/`.pdf` inline; the results panel will offer them as downloads. Confirm the UI file listing surfaces `.pdf`/`.tex` (likely already does via the results dir). A nicer inline PDF preview is a UI3 follow-up, out of scope here.
- **Sandbox surface.** `latex_compile` runs a real binary on model-authored input. Keep it under `setrlimit` + minimal env like `py_exec`, `results/`-only path guard, and no shell — `pdflatex` can execute `\write18`/shell-escape, so ensure shell-escape stays **disabled** (default off; do not pass `-shell-escape`).

## Notes
- Format control today is a single prompt line: `research/supervisor_prompt.md:26`. Subagents also write summaries (`prompts/data_analyst.md:9`). Bus reports to the human are separate narrative and stay markdown-rendered — this feature is about the *written deliverable files*, not the chat narration.
- `config_loader.render` already leaves unknown `{{PLACEHOLDER}}` intact, so adding `{{OUTPUT_FORMAT_GUIDANCE}}` is safe even before runtime passes a value.
- Dockerfile's root apt stage runs before the `USER myuser` switch, so adding TeX packages there keeps the non-root runtime posture (Rule 1) intact.
- Roadmap ID R13 (next free after R12; R11 reserved for Isambard/ARM64).

### Implementation log (2026-07-09)
- **Deviation — step 6 registry.** Wired `latex_compile` via the *generic* `tools/<name>/server.py` loader in `tools_registry.build_tools` instead of an explicit branch. The `ocr` tool set the precedent (there's even `test_ocr_wires_via_generic_loader` enforcing it), so no scaffold edit was needed — just the two role YAMLs. Fewer scaffold changes, matches convention.
- **Deviation — step 5 aux handling.** Compile runs with `-output-directory=<scratch work dir>` and `cwd=results/` (so `\includegraphics` resolves relative figures). Only the final `.pdf` is moved into `results/`; aux/log stay in the throwaway dir and are `rmtree`'d. This keeps `results/` clean (no `.aux/.log` litter) and means a failed compile never half-writes the deliverable — a nicer outcome than "clean aux files" after the fact.
- **Security — shell-escape.** `-no-shell-escape` passed explicitly; verified on host that `\immediate\write18{touch PWNED}` does **not** execute. Covered by `test_latex_compile_no_shell_escape`.
- **Fix.** Module docstring made raw (`r"""`) — `\write18` in prose triggered a `SyntaxWarning: invalid escape sequence`.
- **Dockerfile.** The root apt block already had `tesseract-ocr`/`poppler-utils` (from the `ocr` tool); appended `texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended latexmk` there. Non-root gate (`test_dockerfile_runs_nonroot`) still green.
- **Verification done.** (1) Host: real `pdflatex` E2E — happy path produces a 58 KB PDF with `results/` clean; broken `.tex` → error + log tail + `.tex` preserved; shell-escape blocked. (2) Full suite: 62 passed, 1 skipped, no regressions. (3) In the running (bind-mounted, no-texlive) container: `OUTPUT_FORMAT=latex`, supervisor prompt carries the latex guidance with placeholder substituted, `latex_compile` wired for supervisor+data_analyst, and the no-compiler path degrades gracefully (actionable error, `.tex` preserved).
- **Step 9 — live agent run DONE (2026-07-09).** Ran a throwaway API container from `yuhe_coscientist:r13-verify` on `127.0.0.1:8799` sharing `./state` (the user's `:8765` stack untouched). A real supervisor session in latex mode wrote `results/exponential_growth.tex` (a proper `\documentclass[11pt]{article}` doc with amsmath/amssymb/hyperref + a displayed `N(t)=N_0 e^{\mu t}` equation), called `mcp__latex_compile__render`, and produced `results/exponential_growth.pdf` (55 KB, valid PDF-1.7). `results/` held only the `.tex` + `.pdf` — no aux leak. Markdown toggle re-confirmed in-container (env=markdown → prompt carries `.md` guidance, no `latex_compile` instruction). Session finished with no checkpoint. Evidence: `state/users/u_c775f23bca367c49/root/state/sessions/20260709-205032-9dc618/results/`.
