"""Compile a LaTeX source file (living in the session results/ dir) to PDF (R13).

The supervisor, in the default `latex` output mode, writes its final deliverable
as a self-contained `.tex` under results/ and then calls this tool to produce the
`.pdf` alongside it.

Safety posture (mirrors py_exec / ocr — the guard is in the tool, not the prompt):
- Path guard: the target `.tex` must resolve **inside the session results/ dir**.
  No scratch/, no researcher_data/, no traversal, no absolute escape.
- The compiler runs in a subprocess under `setrlimit` (CPU/mem/file-size) with a
  wall-clock timeout and an explicit minimum environment (no HOME leak, no keys).
- **Shell-escape stays disabled** (`-no-shell-escape`): a `.tex` can otherwise run
  arbitrary shell via `\write18`, and this input is model-authored.
- Aux files (.aux/.log/.out/…) are written to a throwaway scratch dir via
  `-output-directory`; only the final `.pdf` lands in results/, so results/ stays
  clean and a failed run never half-writes the deliverable.

Compilers: prefer `latexmk -pdf` (handles the rerun-for-refs dance) when present;
otherwise `pdflatex` run twice. Both are apt-installed in the container image; if
neither is found the tool returns an actionable error instead of crashing.

See docs/plans/R13-latex-output.md."""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings

# Bounds — a report compile, not a batch job.
_CPU_SECONDS = int(os.environ.get("COSCIENTIST_LATEX_CPU_SECONDS", "120"))
_MEM_MB = int(os.environ.get("COSCIENTIST_LATEX_MEM_MB", "2048"))
_TIMEOUT = int(os.environ.get("COSCIENTIST_LATEX_TIMEOUT_S", "180"))  # wall-clock
_LOG_TAIL = 4000  # chars of the .log returned on failure


def _allowed_roots(session_id: str) -> list[Path]:
    # results/ ONLY — the deliverable dir. Narrower than fs_write_workspace on
    # purpose: we compile deliverables, not scratch experiments.
    return [(settings.session_dir(session_id) / "results").resolve()]


def _candidate_paths(session_id: str, p: str) -> list[Path]:
    raw = Path(p).expanduser()
    sd = settings.session_dir(session_id)
    candidates: list[Path] = []
    if not raw.is_absolute():
        parts = raw.parts
        if parts and parts[0] == "results":
            candidates.append(sd / raw)
        candidates.append(settings.ROOT / raw)
    candidates.append(raw)
    return [candidate.resolve() for candidate in candidates]


def _resolve_safe(session_id: str, p: str) -> Path:
    target: Path | None = None
    for target in _candidate_paths(session_id, p):
        for root in _allowed_roots(session_id):
            if target == root or root in target.parents:
                return target
    raise PermissionError(f"path outside the session results/ dir: {target}")


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "isError": True}


def _set_limits() -> None:
    cpu, mem = _CPU_SECONDS, _MEM_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except (ValueError, OSError):
        pass


def _min_env(work: Path) -> dict[str, str]:
    # Explicit minimum environment. HOME points at the throwaway work dir so
    # texlive can write font/format caches somewhere harmless and writable.
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(work),
        "TEXMFVAR": str(work / "texmf-var"),
        "SOURCE_DATE_EPOCH": "0",  # reproducible output, no clock read
    }


async def _run(argv: list[str], *, cwd: Path, work: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=_min_env(work),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_set_limits,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return (
        proc.returncode,
        (stdout or b"").decode("utf-8", errors="replace"),
        (stderr or b"").decode("utf-8", errors="replace"),
    )


def _log_tail(work: Path, stem: str) -> str:
    log = work / f"{stem}.log"
    if not log.exists():
        return ""
    text = log.read_text(encoding="utf-8", errors="replace")
    return text[-_LOG_TAIL:]


def make_tools(session_id: str):
    @tool(
        "render",
        "Compile a LaTeX source file in the session results/ dir to PDF. Pass "
        "path='results/<name>.tex'; on success the PDF is written to "
        "results/<name>.pdf and its path returned. On failure the tail of the "
        "LaTeX log is returned so you can fix the .tex and retry — the .tex is "
        "never modified or deleted. Shell-escape is disabled.",
        {"path": str},
    )
    async def render(args: dict[str, Any]) -> dict:
        raw_path = (args.get("path") or "").strip()
        if not raw_path:
            return _err("path is required (e.g. 'results/report.tex')")
        try:
            src = _resolve_safe(session_id, raw_path)
        except PermissionError as e:
            return _err(str(e))
        if src.suffix.lower() != ".tex":
            return _err(f"not a .tex file: {src.name}")
        if not src.exists() or not src.is_file():
            return _err(f"not a file: {src}")

        latexmk = shutil.which("latexmk")
        pdflatex = shutil.which("pdflatex")
        if not latexmk and not pdflatex:
            return _err(
                "no LaTeX compiler found (need 'latexmk' or 'pdflatex'). Add "
                "'texlive-latex-recommended texlive-latex-extra latexmk' to the "
                "Dockerfile apt-install and rebuild (keep the non-root USER)."
            )

        stem = src.stem
        results_dir = src.parent
        work = settings.session_dir(session_id) / "scratch" / f"latex-{uuid.uuid4().hex[:8]}"
        (work / "texmf-var").mkdir(parents=True, exist_ok=True)

        # cwd = results/ so \includegraphics resolves relative figure paths;
        # -output-directory = throwaway work dir so aux files never touch results/.
        common = ["-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
                  f"-output-directory={work}"]
        if latexmk:
            passes = [[latexmk, "-pdf", f"-pdflatex=pdflatex {' '.join(common)}",
                       "-interaction=nonstopmode", "-halt-on-error",
                       f"-output-directory={work}", src.name]]
        else:
            # Two passes so cross-references / ToC settle.
            passes = [[pdflatex, *common, src.name], [pdflatex, *common, src.name]]

        rc, out, err = 0, "", ""
        try:
            for argv in passes:
                rc, out, err = await _run(argv, cwd=results_dir, work=work)
                if rc != 0:
                    break
        except asyncio.TimeoutError:
            shutil.rmtree(work, ignore_errors=True)
            return _err(
                f"LaTeX compile timed out after {_TIMEOUT}s. Simplify the document "
                "(e.g. fewer/simpler figures) or split it."
            )

        pdf = work / f"{stem}.pdf"
        if rc != 0 or not pdf.exists():
            tail = _log_tail(work, stem) or (err.strip() or out.strip())
            shutil.rmtree(work, ignore_errors=True)
            return _err(
                f"LaTeX compile failed (rc={rc}). Fix the .tex and retry. "
                f"The .tex was left untouched at results/{src.name}.\n"
                f"--- log tail ---\n{tail}"
            )

        out_pdf = results_dir / f"{stem}.pdf"
        shutil.move(str(pdf), str(out_pdf))
        shutil.rmtree(work, ignore_errors=True)
        size = out_pdf.stat().st_size
        return {"content": [{"type": "text", "text":
            f"compiled results/{src.name} -> results/{out_pdf.name} ({size} bytes)"}]}

    return [render]


def make_server(session_id: str):
    return create_sdk_mcp_server("latex_compile", "0.1.0", tools=make_tools(session_id))
