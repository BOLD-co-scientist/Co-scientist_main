"""R13 — LaTeX-by-default output. Unit + (host-gated) compile tests.

No API calls. The real pdflatex roundtrip is skipped on hosts without a LaTeX
compiler; it runs inside the container image (texlive is apt-installed there).
See docs/plans/R13-latex-output.md."""
from __future__ import annotations

import asyncio
import importlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- setting -----------------------------------------------------------------

@pytest.mark.parametrize("env,expected", [
    (None, "latex"),          # default
    ("latex", "latex"),
    ("markdown", "markdown"),
    ("MarkDown", "markdown"),  # case-insensitive
    ("  latex  ", "latex"),    # trimmed
    ("garbage", "latex"),      # bad value falls back
    ("", "latex"),             # empty falls back
])
def test_output_format_setting(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("COSCIENTIST_OUTPUT_FORMAT", raising=False)
    else:
        monkeypatch.setenv("COSCIENTIST_OUTPUT_FORMAT", env)
    from scaffold import settings as s
    importlib.reload(s)
    assert s.OUTPUT_FORMAT == expected


# --- guidance loader ---------------------------------------------------------

def test_guidance_blocks(monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(ROOT))
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import config_loader as c
    importlib.reload(c)

    tex = c.output_format_guidance("latex")
    md = c.output_format_guidance("markdown")

    assert "latex_compile.render" in tex
    assert r"\documentclass" in tex
    assert "never delete or lose" in tex.lower() or "never delete" in tex
    assert tex  # non-empty

    assert "Markdown" in md
    # markdown mode must not tell the agent to emit a full LaTeX document
    assert r"\documentclass{article}" not in md.replace("do not emit a\n  full ", "")

    # unknown format falls back to the latex block (never empty)
    assert c.output_format_guidance("nonsense") == tex


def test_guidance_tracks_setting(monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(ROOT))
    monkeypatch.setenv("COSCIENTIST_OUTPUT_FORMAT", "markdown")
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import config_loader as c
    importlib.reload(c)
    # no-arg call uses settings.OUTPUT_FORMAT
    assert c.output_format_guidance() == c.output_format_guidance("markdown")


def test_supervisor_prompt_has_placeholder():
    text = (ROOT / "research" / "supervisor_prompt.md").read_text(encoding="utf-8")
    assert "{{OUTPUT_FORMAT_GUIDANCE}}" in text


def test_rendered_prompt_substitutes_guidance(monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(ROOT))
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import config_loader as c
    importlib.reload(c)
    sup = c.load_supervisor()
    rendered = c.render_prompt(
        sup, TASK="t", SUBAGENT_CATALOG="cat", SESSION_ID="sid",
        OUTPUT_FORMAT_GUIDANCE=c.output_format_guidance("latex"),
    )
    assert "{{OUTPUT_FORMAT_GUIDANCE}}" not in rendered
    assert "latex_compile.render" in rendered


# --- tool wiring + path guard ------------------------------------------------

def test_latex_compile_wires_via_generic_loader(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import tools_registry
    importlib.reload(tools_registry)
    s.ensure_session_dirs("lc-wire")
    servers, allowed = tools_registry.build_tools("lc-wire", "supervisor", ["latex_compile"])
    assert "latex_compile" in servers
    assert "mcp__latex_compile" in allowed


def test_latex_compile_server_constructs(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    s.ensure_session_dirs("lc-c")
    assert lc.make_server("lc-c") is not None


def test_latex_compile_path_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    s.ensure_session_dirs("lc-g")
    # results/ path accepted
    good = lc._resolve_safe("lc-g", "results/report.tex")
    assert good == (s.session_dir("lc-g") / "results" / "report.tex").resolve()
    # everything outside results/ rejected
    for bad in ["scratch/x.tex", "../escape.tex", "/etc/passwd",
                "results/../../evil.tex", "memory/x.tex"]:
        with pytest.raises(PermissionError):
            lc._resolve_safe("lc-g", bad)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_latex_compile_rejects_non_tex(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    s.ensure_session_dirs("lc-nt")
    tool = lc.make_tools("lc-nt")[0]
    (s.session_dir("lc-nt") / "results" / "x.md").write_text("hi", encoding="utf-8")
    res = _run(tool.handler({"path": "results/x.md"}))
    assert res.get("isError")
    assert "not a .tex" in res["content"][0]["text"]

    # missing path arg
    res2 = _run(tool.handler({"path": ""}))
    assert res2.get("isError")


# --- real compile (host-gated) -----------------------------------------------

_HAS_LATEX = shutil.which("latexmk") is not None or shutil.which("pdflatex") is not None


@pytest.mark.skipif(not _HAS_LATEX, reason="no LaTeX compiler on host (present in the image)")
def test_latex_compile_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    sid = "lc-run"
    s.ensure_session_dirs(sid)
    results = s.session_dir(sid) / "results"
    (results / "report.tex").write_text(
        r"\documentclass{article}\usepackage{amsmath}"
        r"\begin{document}Hello $E=mc^2$.\end{document}",
        encoding="utf-8",
    )
    tool = lc.make_tools(sid)[0]
    res = _run(tool.handler({"path": "results/report.tex"}))
    assert not res.get("isError"), res["content"][0]["text"]
    assert (results / "report.pdf").exists()
    assert (results / "report.pdf").stat().st_size > 0
    # results/ stays clean of aux files
    leaked = [p.name for p in results.iterdir()
              if p.suffix in (".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk")]
    assert not leaked, f"aux files leaked into results/: {leaked}"


@pytest.mark.skipif(not _HAS_LATEX, reason="no LaTeX compiler on host (present in the image)")
def test_latex_compile_failure_preserves_tex(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    sid = "lc-fail"
    s.ensure_session_dirs(sid)
    results = s.session_dir(sid) / "results"
    (results / "bad.tex").write_text(
        r"\documentclass{article}\begin{document}\nosuchcmd{x}\end{document}",
        encoding="utf-8",
    )
    tool = lc.make_tools(sid)[0]
    res = _run(tool.handler({"path": "results/bad.tex"}))
    assert res.get("isError")
    assert (results / "bad.tex").exists(), "the .tex must survive a failed compile"
    assert not (results / "bad.pdf").exists()
    assert "log tail" in res["content"][0]["text"]


@pytest.mark.skipif(not _HAS_LATEX, reason="no LaTeX compiler on host (present in the image)")
def test_latex_compile_no_shell_escape(tmp_path, monkeypatch):
    """Security: \\write18 must NOT execute (shell-escape stays disabled)."""
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from tools.latex_compile import server as lc
    importlib.reload(lc)
    sid = "lc-sec"
    s.ensure_session_dirs(sid)
    results = s.session_dir(sid) / "results"
    marker = results / "PWNED"
    (results / "evil.tex").write_text(
        r"\documentclass{article}\begin{document}"
        r"\immediate\write18{touch PWNED}Hi\end{document}",
        encoding="utf-8",
    )
    tool = lc.make_tools(sid)[0]
    _run(tool.handler({"path": "results/evil.tex"}))
    assert not marker.exists(), "SECURITY REGRESSION: shell-escape executed"
