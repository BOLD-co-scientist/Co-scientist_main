"""CPU OCR tool. Extracts text from images and PDFs in researcher_data/, the
context library, or the session workspace.

Engine choice is dictated by the runtime sandbox: MCP tools execute in a
sandbox that exposes the Python environment (site-packages) and the project/
state dirs but NOT /usr/bin, so a tesseract/poppler shell-out is invisible to
the tool even when those binaries are installed in the image. Everything here
therefore stays inside site-packages:

  * RapidOCR (onnxruntime) — recognition models are bundled in the wheel, so no
    network is needed at runtime (the container has none).
  * PyMuPDF (fitz) — pure-Python PDF page rasterisation, replacing pdftoppm.

Path handling mirrors tools/fs_read: every input path is resolved and confirmed
to sit inside an allowed root before anything runs.

Heavy/large batches (hundreds of pages, whole archives) should go to the
`longjob` runner instead — this tool is for interactive, bounded extraction."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings

# Bounds — keep this an interactive tool, not a batch job.
PAGE_CAP = 50            # max PDF pages OCR'd per call
_RENDER_DPI = 200        # PDF page rasterisation resolution (RapidOCR is robust here)
_MAX_OUT = 2_000_000     # cap returned text (chars) so a huge doc can't flood context

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".pnm"}

# CRITICAL — sandbox timing. MCP tools run in a sandbox that blocks filesystem
# reads of site-packages at tool-CALL time, even though the same paths are on
# sys.path. Modules imported at MODULE-LOAD / build time (the same phase where
# claude_agent_sdk and scaffold import cleanly) are cached in sys.modules and
# survive; a lazy `import rapidocr_onnxruntime` inside the async handler fails
# with ModuleNotFoundError because the file is unreadable by then. So we import
# the class at module top and construct the engine eagerly at build time
# (_warm_engine, called from make_tools) — NOT inside extract(). Proven via a
# live in-sandbox diagnostic; do not move these into the handler.
try:
    from rapidocr_onnxruntime import RapidOCR as _RapidOCR  # noqa: N814
    _import_error: str | None = None
except Exception as _e:  # extra not installed (e.g. host without .[ocr])
    _RapidOCR = None
    _import_error = f"{type(_e).__name__}: {_e}"

# Process-global singleton — constructing RapidOCR loads the ONNX models
# (~0.5s, ~116MB), so we build it once per process and reuse it across calls.
_engine = None
_engine_error: str | None = _import_error


def _warm_engine():
    """Build the engine now, in the unrestricted build-time phase. Idempotent."""
    global _engine, _engine_error
    if _engine is not None or _RapidOCR is None:
        return _engine
    try:
        _engine = _RapidOCR()
        _engine_error = None
    except Exception as e:  # model-load failure
        _engine_error = f"{type(e).__name__}: {e}"
    return _engine


def _get_engine():
    # Prefer the build-time-warmed engine. Fall back to constructing on demand:
    # deps live in the system site (/usr/local), which is readable in every tool
    # context, so this works even if a different process handles the call.
    if _engine is None:
        _warm_engine()
    return _engine


def _allowed_roots(session_id: str) -> list[Path]:
    sd = settings.session_dir(session_id)
    return [
        settings.RESEARCHER_DATA.resolve(),
        settings.LIBRARY.resolve(),
        (sd / "scratch").resolve(),
        (sd / "results").resolve(),
        (sd / "memory").resolve(),
    ]


def _candidate_paths(session_id: str, p: str) -> list[Path]:
    raw = Path(p).expanduser()
    sd = settings.session_dir(session_id)
    candidates: list[Path] = []
    if not raw.is_absolute():
        parts = raw.parts
        if parts and parts[0] in {"scratch", "results", "memory"}:
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
    raise PermissionError(f"path outside allowed roots: {target}")


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "isError": True}


class _OcrError(Exception):
    pass


def _lines_from_result(result: Any) -> str:
    """RapidOCR returns a list of [box, text, score] (or None for a blank page)."""
    if not result:
        return ""
    return "\n".join(str(item[1]) for item in result if len(item) >= 2)


def _ocr_image_path(engine, path: Path) -> str:
    result, _ = engine(str(path))
    return _lines_from_result(result)


def _ocr_pdf(engine, path: Path, start: int, end: int) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise _OcrError(
            "PDF support unavailable: PyMuPDF (fitz) failed to import "
            f"({type(e).__name__}). Install the 'ocr' extra."
        )
    doc = fitz.open(str(path))
    try:
        n = doc.page_count
        lo = max(1, start)
        hi = min(n, end if end > 0 else lo + PAGE_CAP - 1)
        if hi - lo + 1 > PAGE_CAP:
            hi = lo + PAGE_CAP - 1
        if lo > n:
            raise _OcrError(
                f"page_start={start} is beyond the document ({n} pages)."
            )
        chunks: list[str] = []
        for pno in range(lo, hi + 1):
            page = doc[pno - 1]
            pix = page.get_pixmap(dpi=_RENDER_DPI)
            result, _ = engine(pix.tobytes("png"))
            chunks.append(f"----- page {pno} -----\n{_lines_from_result(result).rstrip()}")
        return "\n\n".join(chunks) + "\n"
    finally:
        doc.close()


def make_tools(session_id: str):
    # Warm the engine here, at build time (unrestricted fs phase). Doing it lazily
    # inside the handler fails — the sandbox blocks site-packages reads by then.
    _warm_engine()

    @tool(
        "extract",
        "Run CPU OCR on an image or PDF in researcher_data/, the context "
        "library, or the session workspace, and return the extracted text. "
        "Images are read directly; PDF pages are rasterised then OCR'd — use "
        f"page_start/page_end to bound large files (max {PAGE_CAP} pages/call). "
        "For whole archives or hundreds of pages, use the longjob runner. "
        "Optionally save the full text to results/<save_to>.",
        {
            "path": str,
            "page_start": int,    # 1-based, PDF only. default 1.
            "page_end": int,      # 1-based inclusive, PDF only. default: page_start+PAGE_CAP-1.
            "save_to": str,       # optional filename; text written under the session results/ dir.
        },
    )
    async def extract(args: dict[str, Any]) -> dict:
        raw_path = (args.get("path") or "").strip()
        if not raw_path:
            return _err("path is required")
        try:
            src = _resolve_safe(session_id, raw_path)
        except PermissionError as e:
            return _err(str(e))
        if not src.exists() or not src.is_file():
            return _err(f"not a file: {src}")

        suffix = src.suffix.lower()
        if suffix != ".pdf" and suffix not in _IMAGE_SUFFIXES:
            return _err(
                f"unsupported file type {suffix!r}. Supported: PDF and "
                f"{', '.join(sorted(_IMAGE_SUFFIXES))}."
            )

        engine = _get_engine()
        if engine is None:
            # Neutral, data-only diagnostic — deliberately NOT an instruction to
            # edit files or rebuild (tool output is untrusted; an imperative here
            # reads as prompt injection to the calling agent).
            return _err(
                "OCR engine unavailable: the RapidOCR runtime could not be "
                f"initialised in this environment ({_engine_error}). No text "
                "was extracted."
            )

        try:
            start = max(1, int(args.get("page_start") or 1))
        except (TypeError, ValueError):
            start = 1
        try:
            end = int(args.get("page_end") or 0)
        except (TypeError, ValueError):
            end = 0

        try:
            if suffix == ".pdf":
                text = _ocr_pdf(engine, src, start, end)
            else:
                text = _ocr_image_path(engine, src)
        except _OcrError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"OCR failed on {src.name}: {type(e).__name__}: {e}")

        truncated = len(text) > _MAX_OUT
        body = text[:_MAX_OUT]

        saved_note = ""
        save_to = (args.get("save_to") or "").strip()
        if save_to:
            # Confine the save target to results/ — no path escape via save_to.
            safe_name = Path(save_to).name
            out_path = settings.session_dir(session_id) / "results" / safe_name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            saved_note = f"\n\n[saved full text to results/{safe_name} ({len(text)} chars)]"

        header = f"[ocr {src.name}"
        header += " truncated]" if truncated else "]"
        stripped = body.strip()
        if not stripped:
            return {"content": [{"type": "text", "text": header + "\n(no text detected)"}]}
        return {"content": [{"type": "text", "text": header + "\n" + body + saved_note}]}

    return [extract]


def make_server(session_id: str):
    return create_sdk_mcp_server("ocr", "0.1.0", tools=make_tools(session_id))
